"""Service for handling incoming WhatsApp messages."""

from __future__ import annotations

import asyncio
from difflib import get_close_matches
import json
import logging
import re
import threading
from contextvars import copy_context
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
import django.db.utils
from django.db import close_old_connections, connections, transaction
import functools
from django.utils import timezone
from apps.catalog.models import Product
from apps.core.currencies import format_price
from apps.core.models import Company, UserProfile, WaitlistEntry
from apps.inventory.models import InventoryAdjustment
from apps.sales.models import (
    ChangeRecord,
    CustomerCreditBalance,
    CustomerCreditTransaction,
    DebtorRecord,
    DraftSale,
    DraftSaleItem,
    Sale,
    SaleItem,
)
from apps.whatsapp.models import PendingAction
from apps.sales.services import create_sale_from_parsed_items, PriceOverflowError
from apps.whatsapp.services.business_reports import (
    CLOSING_ACK_TEXT,
    CLOSING_PROMPT_TEXT,
    build_business_snapshot,
    format_business_summary,
    format_low_stock_summary,
    format_profit_summary,
    maybe_send_daily_notifications,
    parse_closing_time_text,
    send_daily_closing_prompt,
    send_daily_summary,
    send_low_stock_summary,
    send_profit_summary,
    set_company_daily_closing_time,
)
from apps.whatsapp.services.message_parser import parse_message_unified
from apps.whatsapp.services.whatsapp_client import get_whatsapp_client
from services.whatsapp.intent_parser import IntentParser
from services.openrouter.client import OpenRouterError
from utils.timing import start_tracking, end_tracking, track

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

PRICING_FEATURES_ENABLED = True
DEBT_CHANGE_RECORDING_ENABLED = False
RULE_BASED_INTENT_PARSER = IntentParser()


PAYMENT_KEYWORDS = (
    "given",
    "gave",
    "give",
    "give me",
    "gave me",
    "paid",
    "pay",
    "received",
    "receive",
    "customer gave",
    "customer give",
    "customer gave me",
    "customer give me",
    "customer paid",
    "customer wapa",
    "wapa",
    "ndapa",
    "ndapihwa",
    "ndapihwiwa",
    "ndapuhwiwa",
    "ndapuwa",
    "apiwa",
    "akapa",
    "ndakapiwa",
    "ndapiwa",
    "akapihwa",
    "bhadhara",
    "abhadhara",
    "mari",
    "vakandipa",
    "vakandipawo",
)

PAYMENT_KEYWORD_PATTERN = (
    r"(?:given|gave|give(?:\s+me)?|gave\s+me|paid|pay|received|receive|"
    r"customer\s+(?:gave|give)(?:\s+me)?|customer\s+paid|customer\s+wapa|wapa|"
    r"ndapa|ndapihwa|ndapihwiwa|ndapuhwiwa|ndapuwa|ndapuwa|apiwa|akapa|ndakapiwa|"
    r"ndapiwa|akapihwa|bhadhara|abhadhara|mari|vakandipa|vakandipawo)"
)
CURRENCY_WORD_PATTERN = r"(?:usd|us\s*dollars?|dollars?|dolars?|dollers?|zig|zwg|zar|rands?|rand|bwp|pula|eur|euro|gbp|pounds?)"


def _normalize_message_for_matching(text: str) -> str:
    """Normalize whitespace/punctuation for intent matching while keeping numeric text intact."""
    normalized = (text or "").lower().strip()
    normalized = re.sub(r"[\s\u00A0]+", " ", normalized)
    return normalized


def _has_payment_intent(text: str) -> bool:
    normalized = _normalize_message_for_matching(text)
    return bool(re.search(PAYMENT_KEYWORD_PATTERN, normalized, re.IGNORECASE))


def _currency_from_token(token: str, default_currency: str | None = None) -> str:
    token = re.sub(r"\s+", " ", (token or "").lower().strip())
    if token in ("$", "usd", "dollar", "dollars", "us dollar", "us dollars", "dolar", "dolars", "doller", "dollers"):
        return "USD"
    if token in ("r", "zar", "rand", "rands"):
        return "ZAR"
    if token in ("p", "bwp", "pula"):
        return "BWP"
    if token in ("zig", "zwg"):
        return "ZWG"
    if token in ("eur", "euro", "€"):
        return "EUR"
    if token in ("gbp", "pounds", "£"):
        return "GBP"
    return default_currency or "USD"

FINALIZE_KEYWORDS = (
    "done",
    "finish",
    "thats all",
    "that's all",
    "ndapedza",
    "pedza",
)

GREETING_TOKENS = (
    "hi",
    "hie",
    "hello",
    "hey",
    "murisei",
    "makadini",
    "makadaini",
    "mhoro",
)

POSITIVE_CONFIRMATION_TOKENS = (
    "ok",
    "okay",
    "k",
    "kk",
    "thanks",
    "thank",
    "thankyou",
    "thank-you",
    "thx",
    "yes",
    "yep",
    "yeah",
    "yup",
    "sure",
    "alright",
    "fine",
    "cool",
    "bet",
    "nhai",
    "ndatenda",
    "thanksyou",
    "etc",
    "rtc",
)

NEGATIVE_CONFIRMATION_TOKENS = (
    "no",
    "nope",
    "nah",
    "nahh",
    "noo",
    "never",
    "not",
    "negative",
)

POSITIVE_CONFIRMATION_PHRASES = (
    "sounds good",
    "looks good",
    "all good",
    "that works",
    "go ahead",
)

NEGATIVE_CONFIRMATION_PHRASES = (
    "no thanks",
    "no thank you",
    "not really",
    "not yet",
    "i do not think so",
    "i don't think so",
)

PENDING_ACTION_RECORD_CHANGE_NAME = PendingAction.ActionType.RECORD_CHANGE_NAME
PENDING_ACTION_RECORD_DEBTOR_NAME = "record_debtor_name"
PENDING_ACTION_REMOVE_DEBT_CONFIRM = "remove_debt_confirm"
PENDING_ACTION_REMOVE_CHANGE_CONFIRM = "remove_change_confirm"

ONBOARDING_STAGE_AWAIT_SHOP_NAME = "await_shop_name"
ONBOARDING_STAGE_AWAIT_ROLE = "await_role"
ONBOARDING_STAGE_DONE = "done"

_DEBT_QUERY_TOKENS = ("debt", "debtor", "owe", "owes", "ows", "chikwereti")
_CHANGE_QUERY_TOKENS = ("change", "chinja", "credit", "credits")
_QUERY_HINT_TOKENS = (
    "who",
    "how much",
    "how many",
    "which",
    "show",
    "list",
    "have",
    "has",
    "ndiani",
    "mangani",
    "ane",
    "yakadii",
)
_NAME_STOPWORDS = {
    "who", "how", "much", "many", "has", "have", "do", "we", "in", "this", "day", "week",
    "month", "year", "today", "yesterday", "debt", "debtor", "debtors", "change", "owes", "owe",
    "ows", "business", "howmuch", "howmany", "ane", "ndiani", "mangani", "chikwereti", "yakadii", "credit", "credits",
}

_TYPO_HINT_VOCAB = {
    # Core query words
    "who", "which", "show", "list", "have", "has", "how", "much", "many",
    # Ledger concepts
    "debt", "debtor", "debtors", "owe", "owes", "ows", "change", "credit", "credits",
    "chikwereti", "chinja", "ndiani", "mangani", "ane", "yakadii",
    # Timeframe words
    "today", "yesterday", "last", "week", "month", "year", "all", "time", "business",
    "nhasi", "nezuro", "vhiki", "mwedzi", "gore", "zuva",
    # Sales query words
    "sell", "sold", "sales", "transaction", "transactions",
}

_MONTH_NAME_TO_NUM = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _looks_like_sale_or_payment_text(text: str) -> bool:
    """Heuristic guard: pending creditor name input should not look like a transaction."""
    normalized = (text or "").lower().strip()
    if not normalized:
        return True

    if any(marker in normalized for marker in ("each", "per", "@", " sold ", " customer ")):
        return True

    if re.search(r"\b(customer\s+(?:gave|give)(?:\s+me)?|gave\s+me|give\s+me|ndapihwa|ndapihwiwa|ndapuhwiwa|ndapuwa|ndapa|vakandipa|vakandipawo)\b", normalized, re.IGNORECASE):
        return True

    if re.search(PAYMENT_KEYWORD_PATTERN, normalized, re.IGNORECASE):
        return True

    if re.search(r"\d", normalized):
        # Names can rarely include digits; treat numeric text as non-name in this flow.
        return True

    if re.search(CURRENCY_WORD_PATTERN, normalized, re.IGNORECASE):
        return True

    return False


def _normalize_query_text_with_typos(text: str) -> str:
    """Normalize text and correct minor misspellings for known query/timeframe terms."""
    normalized = _normalize_message_for_matching(text)
    if not normalized:
        return normalized

    corrected_tokens: list[str] = []
    for token in normalized.split():
        stripped = re.sub(r"[^a-z]", "", token)
        if len(stripped) < 4 or stripped in _TYPO_HINT_VOCAB:
            corrected_tokens.append(token)
            continue

        # Lightweight fuzzy correction for common misspellings like "yesterdat" -> "yesterday".
        matches = get_close_matches(stripped, _TYPO_HINT_VOCAB, n=1, cutoff=0.86)
        corrected_tokens.append(matches[0] if matches else token)

    return " ".join(corrected_tokens)


def _looks_like_customer_name(text: str) -> bool:
    """Accept simple person/shop names while rejecting transaction-like text."""
    candidate = (text or "").strip()
    if len(candidate) < 2 or len(candidate) > 80:
        return False

    if _looks_like_sale_or_payment_text(candidate):
        return False

    # Allow letters, spaces, apostrophes and hyphens.
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'\- ]*[A-Za-z]", candidate))


def _looks_like_shop_name(text: str) -> bool:
    """Accept simple shop names while rejecting obvious transaction text."""
    candidate = (text or "").strip()
    if len(candidate) < 2 or len(candidate) > 80:
        return False

    if _looks_like_sale_or_payment_text(candidate):
        return False

    if not re.search(r"[A-Za-z0-9]", candidate):
        return False

    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9&'().,/\- ]*[A-Za-z0-9]", candidate))


def _extract_tender_amount(text: str, company_currency: str | None = None) -> tuple[Decimal | None, str | None, bool]:
    """Extract tendered amount and currency from free text.

    Returns (amount, currency, has_payment_intent).
    """
    normalized = _normalize_message_for_matching(text)
    has_payment_intent = _has_payment_intent(normalized)

    # Prefer payment-keyword anchored amounts to avoid grabbing unit prices (e.g. "20 rand each ... given 100 rand").
    keyword_symbol_match = re.search(
        rf"{PAYMENT_KEYWORD_PATTERN}[^\d]{{0,30}}([$€£RPr])\s*(\d+(?:\.\d{{1,2}})?)",
        normalized,
        re.IGNORECASE,
    )
    if keyword_symbol_match:
        amount = Decimal(keyword_symbol_match.group(2))
        currency = _currency_from_token(keyword_symbol_match.group(1), company_currency)
        return amount, currency, has_payment_intent

    keyword_word_match = re.search(
        rf"{PAYMENT_KEYWORD_PATTERN}[^\d]{{0,30}}(\d+(?:\.\d{{1,2}})?)\s*({CURRENCY_WORD_PATTERN})\b",
        normalized,
        re.IGNORECASE,
    )
    if keyword_word_match:
        amount = Decimal(keyword_word_match.group(1))
        currency = _currency_from_token(keyword_word_match.group(2), company_currency)
        return amount, currency, has_payment_intent

    keyword_bare_match = re.search(
        rf"{PAYMENT_KEYWORD_PATTERN}[^\d]{{0,30}}(\d+(?:\.\d{{1,2}})?)\b",
        normalized,
        re.IGNORECASE,
    )
    if keyword_bare_match:
        return Decimal(keyword_bare_match.group(1)), (company_currency or "USD"), has_payment_intent

    # Symbol-first, e.g. "$20", "R15", "£7"
    symbol_match = re.search(r"(?<!\w)([$€£RPr])\s*(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if symbol_match:
        amount = Decimal(symbol_match.group(2))
        currency = _currency_from_token(symbol_match.group(1), company_currency)
        return amount, currency, has_payment_intent

    # Number with trailing currency word, e.g. "20 dollars", "20 usd", "20 rand", "10 rands"
    word_match = re.search(
        rf"(\d+(?:\.\d{{1,2}})?)\s*({CURRENCY_WORD_PATTERN})\b",
        normalized,
        re.IGNORECASE,
    )
    if word_match:
        amount = Decimal(word_match.group(1))
        currency = _currency_from_token(word_match.group(2), company_currency)
        return amount, currency, has_payment_intent

    # Bare amount fallback, e.g. "i was given 20"
    bare_match = re.search(r"\b(\d+(?:\.\d{1,2})?)\b", normalized)
    if bare_match and has_payment_intent:
        return Decimal(bare_match.group(1)), (company_currency or "USD"), has_payment_intent

    return None, None, has_payment_intent


def _looks_like_tender_not_unit_price(text: str) -> bool:
    """Heuristic: amount likely means money received, not per-item unit price."""
    normalized = _normalize_message_for_matching(text)
    # If a message explicitly includes per-item markers, keep parsed unit prices.
    # Example: "10 sugar $5 each, i was given $100" should not drop "$5 each".
    if "each" in normalized or "per" in normalized or "@" in normalized:
        return False
    if _has_payment_intent(normalized):
        return True
    # Common short forms that usually mean total paid for basket
    return bool(re.search(r"\b(given|paid|total|change)\b", normalized))


def _is_payment_only_followup(
    text: str,
    parsed_items: list[dict],
    tender_amount: Decimal | None,
    tender_intent: bool,
) -> bool:
    """Detect payment follow-up text that should finalize a draft without adding new items."""
    if not tender_intent or tender_amount is None:
        return False

    normalized = _normalize_message_for_matching(text)
    if any(marker in normalized for marker in ("each", "per", "@")):
        return False

    if not parsed_items:
        return True

    if len(parsed_items) != 1:
        return False

    keyword_amount_pattern = re.compile(
        rf"{PAYMENT_KEYWORD_PATTERN}[^\d]{{0,30}}(?:[$€£RPr]\s*)?\d+(?:\.\d{{1,2}})?",
        re.IGNORECASE,
    )
    has_payment_keyword_amount = bool(keyword_amount_pattern.search(normalized))

    item = parsed_items[0]
    name = (item.get("product_name") or "").strip().lower()

    try:
        qty = int(item.get("quantity") or 1)
    except (TypeError, ValueError):
        qty = 1
    unit_price = item.get("unit_price")
    if qty != 1 or unit_price is None:
        return False

    if Decimal(str(unit_price)) != tender_amount:
        return False

    # Strong signal for payment follow-up: payment-keyword phrase with amount,
    # no per-item markers, single parsed pseudo-item matching tender.
    if has_payment_keyword_amount:
        return True

    # Additional fallback for colloquial phrasing anchored on buyer/cash words.
    if re.search(r"\b(customer|mari|cash)\b", normalized) and name:
        return True

    return False


def _is_finalize_signal(text: str) -> bool:
    """Whether a text indicates finalizing a multi-message draft basket."""
    normalized = (text or "").lower().strip()
    return any(k in normalized for k in FINALIZE_KEYWORDS)


def _strip_pricing_from_items(items: list[dict]) -> list[dict]:
    """Remove any price-related data from parsed items."""
    stripped: list[dict] = []
    for item in items:
        stripped.append(
            {
                "product_name": item.get("product_name"),
                "quantity": item.get("quantity", 1),
                "unit_price": None,
            }
        )
    return stripped


def _format_item_line(
    *,
    lang: str,
    quantity: int,
    product: str,
    unit_price: Decimal | None,
    currency: str | None,
    declared_total_amount: Decimal | None = None,
    prefix: str = "sale",
) -> str:
    """Format an item line for receipts and previews."""
    if unit_price is not None and currency:
        return t(
            f"{prefix}.item_with_price",
            lang=lang,
            quantity=quantity,
            product=product,
            price=format_price(unit_price, currency),
        )

    if declared_total_amount is not None and currency:
        return t(
            f"{prefix}.item_total",
            lang=lang,
            quantity=quantity,
            product=product,
            total=format_price(declared_total_amount, currency),
        )

    return t(f"{prefix}.item_no_price", lang=lang, quantity=quantity, product=product)

def _looks_like_fresh_basket_message(text: str, item_count: int) -> bool:
    """Heuristic for a new basket message that should replace stale draft items.

    Fresh basket examples:
    - "2 bread, 1 salt, 5 uswa, 11 cooking oil"
    - "sold 2 cokes R5 each, 1 chips R2"

    Continuations typically contain words like "also", "add", "plus", "more".
    """
    normalized = _normalize_message_for_matching(text)
    if item_count >= 2:
        if any(marker in normalized for marker in (" also ", " plus ", " add ", " more ", " another ", "and ")):
            return False
        return True

    # Single-item messages: treat as a fresh basket when a clear price hint is present.
    # Examples: "3 salt $4", "1 uswa R15 each", "2 sugar @ $2"
    if item_count == 1:
        price_hint_patterns = (
            r"\$",                # $5, $ 5
            r"\bR\s*\d",       # R15 or R 15
            r"\brand\b",        # word 'rand'
            r"\beach\b",        # 'each'
            r"@",                 # '@' as price marker
            r"\b(usd|eur|gbp|zar|bwp|zwg)\b",  # explicit currency codes/words
            r"\bper\b",         # 'per' as in '10 per item'
        )
        for p in price_hint_patterns:
            if re.search(p, text or "", flags=re.IGNORECASE):
                return True

    return False


def _is_greeting_text(text: str) -> bool:
    """Return True when the message is a short greeting phrase."""
    normalized = _normalize_message_for_matching(text)
    if not normalized:
        return False

    cleaned = re.sub(r"[^a-z\s]", " ", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False

    # Accept short greeting-only phrases like "hello", "hie there", "murisei".
    tokens = cleaned.split()
    if len(tokens) > 4:
        return False
    return any(token in GREETING_TOKENS for token in tokens)


def _classify_confirmation_text(text: str) -> str | None:
    """Classify short confirmation replies as 'positive', 'negative', or None."""
    normalized = _normalize_message_for_matching(text)
    if not normalized:
        return None

    cleaned = re.sub(r"[^a-z\s]", " ", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    tokens = cleaned.split()
    if len(tokens) > 5:
        return None

    for phrase in NEGATIVE_CONFIRMATION_PHRASES:
        if phrase in cleaned:
            return "negative"

    for phrase in POSITIVE_CONFIRMATION_PHRASES:
        if phrase in cleaned:
            return "positive"

    for token in tokens:
        if token in NEGATIVE_CONFIRMATION_TOKENS:
            return "negative"
        if token in POSITIVE_CONFIRMATION_TOKENS:
            return "positive"

    for token in tokens:
        negative_close = get_close_matches(
            token,
            NEGATIVE_CONFIRMATION_TOKENS,
            n=1,
            cutoff=0.8,
        )
        if negative_close:
            return "negative"

        positive_close = get_close_matches(
            token,
            POSITIVE_CONFIRMATION_TOKENS,
            n=1,
            cutoff=0.8,
        )
        if positive_close:
            return "positive"

    return None


def _is_acknowledgement_text(text: str) -> bool:
    """Return True for short acknowledgement replies like 'okay', 'thanks', or 'yes'."""
    return _classify_confirmation_text(text) is not None


def classify_confirmation_text(text: str) -> str | None:
    """Public helper that classifies short confirmation replies."""
    return _classify_confirmation_text(text)


def is_greeting_text(text: str) -> bool:
    """Public helper used by webhook view to route greeting flows."""
    return _is_greeting_text(text)


def is_acknowledgement_text(text: str) -> bool:
    """Public helper used by webhook view for short replies."""
    return _is_acknowledgement_text(text)


def _looks_like_price_update_message(text: str) -> bool:
    """Heuristic for messages that likely provide/clarify a product price."""
    normalized = (text or "").lower().strip()
    price_markers = (
        "price",
        "mutengo",
        "imwe",
        "is",
        "iri",
        "@",
        "each",
        "for",
    )
    return any(marker in normalized for marker in price_markers)


def _extract_management_command(text: str) -> dict | None:
    """Parse sale management commands from free text.

    Supported examples:
    - delete sale 123
    - delete last sale
    - edit sale 123: 2 cokes $1 each
    - edit last sale: 2 cokes $1 each
    """
    raw = (text or "").strip()
    if not raw:
        return None

    normalized = _normalize_message_for_matching(raw)

    delete_last = re.fullmatch(r"(?:delete|remove|bvisa)\s+last\s+sale", normalized, re.IGNORECASE)
    if delete_last:
        return {"action": "delete", "sale_id": None, "use_last": True}

    delete_id = re.fullmatch(r"(?:delete|remove|bvisa)\s+sale\s+#?(\d+)", normalized, re.IGNORECASE)
    if delete_id:
        return {"action": "delete", "sale_id": int(delete_id.group(1)), "use_last": False}

    edit_last = re.fullmatch(r"edit\s+last\s+sale\s*(?:[:\-]\s*)?(.+)", raw, re.IGNORECASE)
    if edit_last:
        return {
            "action": "edit",
            "sale_id": None,
            "use_last": True,
            "new_text": edit_last.group(1).strip(),
        }
    return None


def _detect_ledger_query_type(text: str) -> str | None:
    normalized = _normalize_query_text_with_typos(text)
    if not normalized:
        return None

    has_debt = any(token in normalized for token in _DEBT_QUERY_TOKENS)
    has_change = any(token in normalized for token in _CHANGE_QUERY_TOKENS)
    has_query_hint = any(token in normalized for token in _QUERY_HINT_TOKENS) or "?" in (text or "")

    if has_debt and has_query_hint:
        return "debt"
    if has_change and has_query_hint:
        return "change"
    return None


def _parse_ledger_timeframe(text: str) -> str:
    normalized = _normalize_query_text_with_typos(text)
    specific_date = _extract_specific_date_timeframe(text)
    if specific_date:
        return specific_date

    if "yesterday" in normalized or "yesterdat" in normalized or "nezuro" in normalized:
        return "yesterday"
    if "last week" in normalized:
        return "last_week"
    if "last month" in normalized:
        return "last_month"
    if "last year" in normalized:
        return "last_year"
    if "today" in normalized or "this day" in normalized or "nhasi" in normalized or "zuva ranhasi" in normalized:
        return "today"
    if "this week" in normalized or "week" in normalized or "vhiki" in normalized:
        return "week"
    if "this month" in normalized or "month" in normalized or "mwedzi" in normalized:
        return "month"
    if "this year" in normalized or "year" in normalized:
        return "year"
    if "all time" in normalized or "alltime" in normalized or "in business" in normalized:
        return "all_time"

    days_match = re.search(r"\b(\d+)\s*days?\b", normalized)
    if days_match:
        return f"{days_match.group(1)}_days"

    return "all_time"


def _extract_specific_date_timeframe(text: str) -> str | None:
    """Extract exact-day queries like 'on 12 december' or 'on 2026-12-12'."""
    raw = (text or "").strip()
    if not raw:
        return None

    # ISO-like date: 2026-12-12
    iso_match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        try:
            dt = datetime(year, month, day)
            return f"date_{dt:%Y-%m-%d}"
        except ValueError:
            pass

    # Slash format: 12/12/2026 or 12/12
    slash_match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", raw)
    if slash_match:
        day = int(slash_match.group(1))
        month = int(slash_match.group(2))
        year_raw = slash_match.group(3)
        year = int(year_raw) if year_raw else timezone.now().year
        if year < 100:
            year += 2000
        try:
            dt = datetime(year, month, day)
            return f"date_{dt:%Y-%m-%d}"
        except ValueError:
            pass

    # Text month: 12 december [2026]
    text_match = re.search(
        r"\b(?:on\s+)?(\d{1,2})\s+(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)(?:\s+(\d{4}))?\b",
        raw,
        re.IGNORECASE,
    )
    if text_match:
        day = int(text_match.group(1))
        month = _MONTH_NAME_TO_NUM[text_match.group(2).lower()]
        year = int(text_match.group(3)) if text_match.group(3) else timezone.now().year
        try:
            dt = datetime(year, month, day)
            return f"date_{dt:%Y-%m-%d}"
        except ValueError:
            return None

    return None


def _normalize_sales_query_timeframe(text: str, llm_timeframe: str | None) -> str:
    """Prefer exact date/user wording, then normalize model-provided timeframe."""
    explicit_date = _extract_specific_date_timeframe(text)
    if explicit_date:
        return explicit_date

    raw = (llm_timeframe or "").strip().lower().replace(" ", "_")
    aliases = {
        "this_week": "week",
        "this_month": "month",
        "this_year": "year",
        "last_week": "last_week",
        "last_month": "last_month",
        "last_year": "last_year",
        "past_week": "last_week",
        "past_month": "last_month",
        "past_year": "last_year",
        "all_time": "all_time",
        "alltime": "all_time",
        "in_business": "all_time",
        "today": "today",
        "yesterday": "yesterday",
        "yesterdat": "yesterday",
        "day": "today",
        "week": "week",
        "month": "month",
        "year": "year",
    }
    if raw in aliases:
        return aliases[raw]
    if raw.endswith("_days"):
        return raw
    llm_days_match = re.match(r"^(\d+)\s*_?day(?:s)?$", raw)
    if llm_days_match:
        return f"{llm_days_match.group(1)}_days"

    from_text = _parse_ledger_timeframe(_normalize_query_text_with_typos(text))
    return from_text if from_text != "all_time" else "today"


def _timeframe_label_for_response(timeframe: str, lang: str) -> str:
    if timeframe.startswith("date_"):
        try:
            date_text = timeframe.split("_", 1)[1]
            dt = datetime.strptime(date_text, "%Y-%m-%d")
            return dt.strftime("%d %B %Y")
        except Exception:
            return timeframe
    return _ledger_timeframe_label(timeframe, lang)


def _extract_ledger_subject_name(text: str) -> str | None:
    raw_text = (text or "").strip()
    normalized = _normalize_message_for_matching(raw_text)

    # Patterns like "John have how much change", "Kuda owes how much", "change for John Doe"
    patterns = [
        r"\b(?:for|ya|za)\s+([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})\b",
        r"\b([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})\s+(?:have|has|owes|ows|ane)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if not match:
            continue
        candidate = " ".join(match.group(1).split()).strip()
        tokens = [tok for tok in re.findall(r"[A-Za-z][A-Za-z'\-]*", candidate) if tok.lower() not in _NAME_STOPWORDS]
        if tokens:
            return " ".join(tokens[:3])

    # Fallback: first non-stopword token.
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", normalized)
    for token in tokens:
        lowered = token.lower()
        if lowered in _NAME_STOPWORDS:
            continue
        if len(lowered) < 2:
            continue
        return token.title()
    return None


def _has_explicit_person_reference(text: str) -> bool:
    normalized = _normalize_message_for_matching(text)
    if re.search(r"\bfor\s+[A-Za-z]", text or "", re.IGNORECASE):
        return True
    if re.search(r"\b[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2}\s+(?:have|has|owes|ows|ane)\b", text or "", re.IGNORECASE):
        return True
    return bool(re.search(r"\b(what about|sei|ko)\s+[A-Za-z]", normalized))


def _format_currency_totals(totals_by_currency: dict[str, Decimal]) -> str:
    if not totals_by_currency:
        return "0"
    parts = []
    for currency in sorted(totals_by_currency.keys()):
        parts.append(format_price(totals_by_currency[currency], currency))
    return " + ".join(parts)


def _ledger_timeframe_label(timeframe: str, lang: str) -> str:
    key = f"ledger_query.timeframe_{timeframe}"
    try:
        return t(key, lang=lang)
    except Exception:
        return timeframe


def run_async(coro):
    """
    Run an async coroutine, handling both sync and async contexts.

    In async contexts (like async tests), we can't use asyncio.run() because
    there's already an event loop. We also can't just create a task because
    it won't complete before the caller returns. Instead, we need to run it
    in a thread pool to avoid blocking.

    In sync contexts (like Django views), runs with asyncio.run().
    """
    from django.conf import settings

    ctx = copy_context()

    def _run_coro_in_thread() -> None:
        try:
            ctx.run(asyncio.run, coro)
        except Exception:
            logger.exception("Background async task failed")

    wait_for_completion = "test" in settings.DATABASES["default"]["NAME"]

    in_running_loop = False
    try:
        asyncio.get_running_loop()
        in_running_loop = True
        logger.info("[DEBUG] Event loop detected, dispatching async task")
    except RuntimeError:
        logger.info("[DEBUG] No event loop, dispatching async task")

    if wait_for_completion:
        if in_running_loop:
            worker = threading.Thread(target=_run_coro_in_thread, daemon=True)
            worker.start()
            worker.join(timeout=15)
            if worker.is_alive():
                logger.error("Timed out waiting for background async task in test mode")
        else:
            asyncio.run(coro)
        return

    # Production/runtime mode: do not block webhook request thread.
    threading.Thread(target=_run_coro_in_thread, daemon=True).start()


def db_sync_to_async(func):
    """Like sync_to_async but closes stale DB connections first.

    Django stores DB connections in thread-local storage. sync_to_async runs
    code in an executor thread that may hold a stale connection from a previous
    request. close_old_connections() ensures we don't use a dead connection.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        close_old_connections()
        return func(*args, **kwargs)
    return sync_to_async(wrapper)


# Load localization strings
_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_ALL_STRINGS = {
    "en": json.loads((_LOCALES_DIR / "en.json").read_text()),
    "sn": json.loads((_LOCALES_DIR / "sn.json").read_text()),
}
DEFAULT_LANGUAGE = "sn"


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """Get localized string by dot-notation key, with optional format args."""
    strings = _ALL_STRINGS.get(lang, _ALL_STRINGS[DEFAULT_LANGUAGE])
    keys = key.split(".")
    value = strings
    for k in keys:
        value = value[k]
    return value.format(**kwargs) if kwargs else value


# Admin phone number for waitlist approval notifications
ADMIN_PHONE_NUMBER = "+14342183470"


def _extract_phone_number(sender: str) -> str:
    """
    Extract and normalize phone number.

    Meta sends plain numbers like '1234567890'.
    Stored numbers have '+' prefix like '+1234567890'.
    """
    # Remove whatsapp: prefix if present (backwards compatibility)
    if sender.startswith("whatsapp:"):
        sender = sender[9:]
    # Ensure + prefix for database storage
    if not sender.startswith("+"):
        sender = f"+{sender}"
    return sender


@db_sync_to_async
def _upload_to_r2(media_data: bytes, media_id: str, mime_type: str, phone_number: str) -> str | None:
    """
    Upload media file to R2 storage.

    Args:
        media_data: The raw media file bytes
        media_id: The Meta media ID
        mime_type: The MIME type of the media
        phone_number: The phone number of the sender

    Returns:
        The public URL of the uploaded file, or None if upload failed
    """
    try:
        from services.storage import R2StorageClient
        client = R2StorageClient()
        return client.upload_media(media_data, media_id, mime_type, phone_number)
    except Exception as e:
        logger.error(f"Failed to upload media to R2: {e}", exc_info=True)
        return None


def handle_new_waitlist_entry(sender: str, text: str) -> None:
    """
    Handle a message from an unknown phone number by adding them to the waitlist.

    Args:
        sender: The sender's phone number (e.g., whatsapp:+1234567890)
        text: The message text
    """
    print(f"[DEBUG HANDLER] handle_new_waitlist_entry called for {sender}", flush=True)
    try:
        close_old_connections()
        print(f"[DEBUG HANDLER] About to call run_async", flush=True)
        run_async(_process_new_waitlist_entry_async(sender, text))
        print(f"[DEBUG HANDLER] run_async returned", flush=True)
    except Exception as e:
        print(f"[DEBUG HANDLER] Exception: {e}", flush=True)
        logger.exception(f"Error handling new waitlist entry for {sender}: {e}")


def handle_unapproved_greeting(sender: str, text: str, waitlist_entry: WaitlistEntry | None = None) -> None:
    """Handle greeting from an unapproved user by sending welcome + language buttons."""
    try:
        close_old_connections()
        run_async(_process_unapproved_greeting_async(sender, text, waitlist_entry))
    except Exception as e:
        logger.exception(f"Error handling unapproved greeting for {sender}: {e}")


def handle_known_user_greeting(sender: str, user_profile: UserProfile) -> None:
    """Handle greeting from an approved user by sending a quick starter prompt."""
    try:
        close_old_connections()
        run_async(_process_known_user_greeting_async(sender, user_profile))
    except Exception as e:
        logger.exception(f"Error handling known-user greeting for {sender}: {e}")


def handle_known_user_acknowledgement(
    sender: str,
    user_profile: UserProfile | None,
    reply_to: str | None = None,
) -> None:
    """Handle short confirmation replies that do not map to an active prompt."""
    try:
        close_old_connections()
        run_async(_process_known_user_acknowledgement_async(sender, user_profile, reply_to=reply_to))
    except Exception as e:
        logger.exception(f"Error handling known-user acknowledgement for {sender}: {e}")


def handle_known_language_button_action(lang: str, sender: str, user_profile: UserProfile) -> None:
    """Handle language button click from an approved user."""
    try:
        close_old_connections()
        run_async(_process_known_language_button_async(lang, sender, user_profile.id))
    except Exception as e:
        logger.exception(f"Error handling known-user language selection for {sender}: {e}")


def handle_onboarding_role_button_action(role: str, entry_id: int, sender: str) -> None:
    """Handle owner/worker onboarding role button clicks."""
    try:
        close_old_connections()
        run_async(_process_onboarding_role_button_async(role, entry_id, sender))
    except Exception as e:
        logger.exception(f"Error handling onboarding role selection for {sender}: {e}")


def handle_waitlisted_message(sender: str, text: str, waitlist_entry: WaitlistEntry) -> None:
    """
    Handle a message from a user who is already on the waitlist.

    Args:
        sender: The sender's phone number
        text: The message text
        waitlist_entry: The existing waitlist entry
    """
    try:
        close_old_connections()
        run_async(_process_waitlisted_message_async(sender, text, waitlist_entry))
    except Exception as e:
        logger.exception(f"Error handling waitlisted message for {sender}: {e}")


def handle_incoming_message(
    message_id: str,
    sender: str,
    text: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Handle an incoming WhatsApp message from a known user.

    This function runs the async processing in a new event loop.
    In production, you might want to use Celery or similar for background processing.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        text: The message text
        user_profile: The user profile of the sender
    """
    try:
        # Close old database connections before creating new event loop
        close_old_connections()
        run_async(_process_message_async(message_id, sender, text, user_profile))
    except django.db.utils.OperationalError:
        logger.exception(f"DB connection error for message {message_id}, retrying...")
        for conn in connections.all():
            conn.close()
        run_async(_process_message_async(message_id, sender, text, user_profile))
    except Exception as e:
        logger.exception(f"Error handling message {message_id}: {e}")


def handle_incoming_audio_message(
    message_id: str,
    sender: str,
    media_id: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Handle an incoming WhatsApp audio message from a known user.

    Downloads audio, transcribes it, and processes the transcription.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        media_id: The Meta media ID
        user_profile: The user profile of the sender
    """
    try:
        # Close old database connections before creating new event loop
        close_old_connections()
        run_async(_process_audio_message_async(message_id, sender, media_id, user_profile))
    except django.db.utils.OperationalError:
        logger.exception(f"DB connection error for audio message {message_id}, retrying...")
        for conn in connections.all():
            conn.close()
        run_async(_process_audio_message_async(message_id, sender, media_id, user_profile))
    except Exception as e:
        logger.exception(f"Error handling audio message {message_id}: {e}")


def handle_typing_indicator(sender: str, message_id: str | None = None) -> None:
    """Trigger typing indicator immediately as a best-effort signal."""
    try:
        ctx = copy_context()
        threading.Thread(
            target=lambda: ctx.run(asyncio.run, _send_typing_indicator(sender, message_id=message_id)),
            daemon=True,
        ).start()
    except Exception as e:
        logger.debug("Failed to trigger typing indicator for %s: %s", sender, e)


async def _process_new_waitlist_entry_async(sender: str, text: str) -> None:
    """Add a new user to the waitlist and send language choice buttons."""
    logger.info(f"[DEBUG] _process_new_waitlist_entry_async started for {sender}")
    phone_number = _extract_phone_number(sender)
    logger.info(f"[DEBUG] Phone number: {phone_number}")

    # Create waitlist entry
    logger.info(f"[DEBUG] About to create waitlist entry")
    entry = await _create_waitlist_entry(phone_number, text)
    logger.info(f"[DEBUG] Waitlist entry created: {entry.id if entry else 'None'}")

    # Send greeting message first
    await _send_response(sender, t("waitlist.new_user_greeting"))

    # Send language choice buttons (bilingual prompt since user hasn't chosen yet)
    buttons = [
        {"id": f"lang_en_{entry.id}", "title": t("language.btn_en")},
        {"id": f"lang_sn_{entry.id}", "title": t("language.btn_sn")},
    ]
    message_sid = await _send_response_with_buttons(
        sender, t("language.prompt"), buttons,
    )

    if message_sid:
        await _store_waitlist_response_message_sid(entry.id, message_sid)

    # Send admin notification with approve/reject buttons
    await _send_waitlist_admin_notification(entry)


async def _process_unapproved_greeting_async(
    sender: str,
    text: str,
    waitlist_entry: WaitlistEntry | None = None,
) -> None:
    """Send welcome + language buttons for users not yet approved."""
    entry = waitlist_entry
    if not entry:
        phone_number = _extract_phone_number(sender)
        entry = await _create_waitlist_entry(phone_number, text)

    # Welcome users and explain product value, then ask language.
    await _send_response(sender, t("waitlist.new_user_greeting"))
    buttons = [
        {"id": f"lang_en_{entry.id}", "title": t("language.btn_en")},
        {"id": f"lang_sn_{entry.id}", "title": t("language.btn_sn")},
    ]
    message_sid = await _send_response_with_buttons(sender, t("language.prompt"), buttons)
    if message_sid:
        await _store_waitlist_response_message_sid(entry.id, message_sid)


async def _process_known_user_greeting_async(sender: str, user_profile: UserProfile) -> None:
    """Send approved-user quick-start guidance."""
    lang = user_profile.language or DEFAULT_LANGUAGE
    await _send_response(sender, t("approval.already_approved_greeting", lang=lang))


async def _process_known_user_acknowledgement_async(
    sender: str,
    user_profile: UserProfile | None,
    reply_to: str | None = None,
) -> None:
    """Send a brief guidance message for short confirmation replies without context."""
    lang = user_profile.language if user_profile else DEFAULT_LANGUAGE
    await _send_response(sender, t("chat.continue_prompt", lang=lang), reply_to=reply_to)


async def _process_known_language_button_async(lang: str, sender: str, profile_id: int) -> None:
    """Persist selected language for approved users and confirm switch."""
    target_lang = "en" if lang == "en" else "sn"
    await _update_profile_language(profile_id, target_lang)
    await _send_response(
        sender,
        t(
            "language.changed_anytime",
            lang=target_lang,
            selected=("English" if target_lang == "en" else "Shona"),
        ),
    )
    phone_number = _extract_phone_number(sender)
    entry = await _get_waitlist_entry_by_phone(phone_number)
    if entry:
        await _update_waitlist_language(entry.id, target_lang)
        await _set_waitlist_onboarding_stage(entry.id, ONBOARDING_STAGE_AWAIT_SHOP_NAME)
    await _send_response(sender, t("onboarding.ask_shop_name", lang=target_lang))


async def _process_onboarding_role_button_async(role: str, entry_id: int, sender: str) -> None:
    """Persist onboarding role choice and send getting-started guidance."""
    role_normalized = "owner" if role == "owner" else "worker"
    entry = await _get_waitlist_entry_by_id(entry_id)
    if not entry:
        await _send_response(sender, t("waitlist.already_processed"))
        return

    lang = entry.language or DEFAULT_LANGUAGE
    await _set_waitlist_onboarding_stage(entry_id, ONBOARDING_STAGE_DONE, role=role_normalized)

    if entry.user_profile_id:
        await _update_profile_role_from_onboarding(entry.user_profile_id, role_normalized)

    role_confirm_key = "onboarding.role_saved_owner" if role_normalized == "owner" else "onboarding.role_saved_worker"
    await _send_response(sender, t(role_confirm_key, lang=lang))
    await _send_response(sender, t("onboarding.start_recording", lang=lang))

    if entry.status == WaitlistEntry.Status.PENDING:
        await _send_response(sender, t("waitlist.sales_before_approval", lang=lang))


def handle_language_button_action(
    lang: str,
    entry_id: int,
    sender: str,
) -> None:
    """Handle a language selection button click from a waitlisted user."""
    try:
        close_old_connections()
        run_async(_process_language_button_async(lang, entry_id, sender))
    except Exception as e:
        logger.exception(f"Error handling language selection for {sender}: {e}")


async def _process_language_button_async(lang: str, entry_id: int, sender: str) -> None:
    """Save language choice and send confirmation + waitlist welcome."""
    await _update_waitlist_language(entry_id, lang)

    # Also update UserProfile if user was already approved (race condition:
    # admin may approve before user clicks the language button).
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    if profile:
        await _update_profile_language(profile.id, lang)

    await _set_waitlist_onboarding_stage(entry_id, ONBOARDING_STAGE_AWAIT_SHOP_NAME)
    await _send_response(sender, t("language.confirmed", lang=lang))
    await _send_response(sender, t("onboarding.ask_shop_name", lang=lang))


@db_sync_to_async
def _update_waitlist_language(entry_id: int, language: str) -> None:
    """Update the language on a waitlist entry."""
    WaitlistEntry.objects.filter(id=entry_id).update(language=language)


def _parse_onboarding_notes(notes_text: str | None) -> dict:
    """Safely parse JSON onboarding metadata from WaitlistEntry.notes."""
    if not notes_text:
        return {}
    try:
        parsed = json.loads(notes_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


@db_sync_to_async
def _set_waitlist_onboarding_stage(entry_id: int, stage: str, role: str | None = None) -> None:
    entry = WaitlistEntry.objects.filter(id=entry_id).first()
    if not entry:
        return
    meta = _parse_onboarding_notes(entry.notes)
    meta["onboarding_stage"] = stage
    if role:
        meta["onboarding_role"] = role
    entry.notes = json.dumps(meta)
    entry.save(update_fields=["notes"])


@db_sync_to_async
def _get_waitlist_onboarding_stage(entry_id: int) -> str | None:
    entry = WaitlistEntry.objects.filter(id=entry_id).first()
    if not entry:
        return None
    meta = _parse_onboarding_notes(entry.notes)
    return meta.get("onboarding_stage")


@db_sync_to_async
def _get_waitlist_entry_by_phone(phone_number: str) -> WaitlistEntry | None:
    return WaitlistEntry.objects.filter(phone_number=phone_number).first()


@db_sync_to_async
def _get_waitlist_entry_by_id(entry_id: int) -> WaitlistEntry | None:
    return WaitlistEntry.objects.filter(id=entry_id).first()


@db_sync_to_async
def _update_profile_role_from_onboarding(profile_id: int, role: str) -> None:
    mapped = UserProfile.Role.OWNER if role == "owner" else UserProfile.Role.ASSISTANT
    UserProfile.objects.filter(id=profile_id).update(role=mapped)


@db_sync_to_async
def _update_company_name(company_id: int, name: str) -> None:
    trimmed = (name or "").strip()
    if not trimmed:
        return
    Company.objects.filter(id=company_id).update(name=trimmed)


@db_sync_to_async
def _update_profile_language(profile_id: int, language: str) -> None:
    """Update the language on a user profile."""
    UserProfile.objects.filter(id=profile_id).update(language=language)


@db_sync_to_async
def _get_waitlist_language(phone_number: str) -> str:
    """Get the language for a waitlisted user."""
    try:
        entry = WaitlistEntry.objects.get(phone_number=phone_number)
        return entry.language
    except WaitlistEntry.DoesNotExist:
        return DEFAULT_LANGUAGE


async def _process_waitlisted_message_async(sender: str, text: str, waitlist_entry: WaitlistEntry) -> None:
    """Handle a message from a waitlisted user."""
    lang = waitlist_entry.language
    if waitlist_entry.status == WaitlistEntry.Status.PENDING:
        stage = await _get_waitlist_onboarding_stage(waitlist_entry.id)
        incoming_text = (text or "").strip()

        if stage == ONBOARDING_STAGE_AWAIT_SHOP_NAME:
            if not _looks_like_shop_name(incoming_text):
                await _send_response(sender, t("onboarding.ask_shop_name", lang=lang))
                return

            await _update_waitlist_company_name(waitlist_entry.id, incoming_text)
            await _set_waitlist_onboarding_stage(waitlist_entry.id, ONBOARDING_STAGE_AWAIT_ROLE)
            await _send_response(sender, t("onboarding.shop_name_saved", lang=lang, shop_name=incoming_text))
            await _send_onboarding_role_buttons(sender, lang=lang, entry_id=waitlist_entry.id)
            return

        if stage == ONBOARDING_STAGE_AWAIT_ROLE:
            await _send_onboarding_role_buttons(sender, lang=lang, entry_id=waitlist_entry.id)
            return

        if stage == ONBOARDING_STAGE_DONE and _looks_like_sale_or_payment_text(incoming_text):
            await _send_response(sender, t("waitlist.sales_before_approval", lang=lang))
            return

        # Backward-compatible fallback for older users without onboarding stage saved.
        if not waitlist_entry.company_name and _looks_like_shop_name(incoming_text):
            await _update_waitlist_company_name(waitlist_entry.id, incoming_text)
            await _set_waitlist_onboarding_stage(waitlist_entry.id, ONBOARDING_STAGE_AWAIT_ROLE)
            await _send_response(sender, t("onboarding.shop_name_saved", lang=lang, shop_name=incoming_text))
            await _send_onboarding_role_buttons(sender, lang=lang, entry_id=waitlist_entry.id)
            return

        if _looks_like_sale_or_payment_text(incoming_text):
            await _send_response(sender, t("waitlist.sales_before_approval", lang=lang))
            return

        await _send_response(sender, t("waitlist.still_pending", lang=lang))
    elif waitlist_entry.status == WaitlistEntry.Status.REJECTED:
        await _send_response(sender, t("waitlist.rejected", lang=lang))


async def _maybe_handle_known_user_onboarding_step(
    sender: str,
    text: str,
    user_profile: UserProfile,
    lang: str,
) -> bool:
    """Capture approved-user onboarding inputs after language selection."""
    phone_number = _extract_phone_number(sender)
    entry = await _get_waitlist_entry_by_phone(phone_number)
    if not entry:
        return False

    stage = await _get_waitlist_onboarding_stage(entry.id)
    incoming_text = (text or "").strip()

    if stage == ONBOARDING_STAGE_AWAIT_SHOP_NAME:
        if not _looks_like_shop_name(incoming_text):
            await _send_response(sender, t("onboarding.ask_shop_name", lang=lang))
            return True

        await _update_waitlist_company_name(entry.id, incoming_text)
        await _update_company_name(user_profile.company_id, incoming_text)
        await _set_waitlist_onboarding_stage(entry.id, ONBOARDING_STAGE_AWAIT_ROLE)
        await _send_response(sender, t("onboarding.shop_name_saved", lang=lang, shop_name=incoming_text))
        await _send_onboarding_role_buttons(sender, lang=lang, entry_id=entry.id)
        return True

    if stage == ONBOARDING_STAGE_AWAIT_ROLE:
        await _send_onboarding_role_buttons(sender, lang=lang, entry_id=entry.id)
        return True

    return False


async def _send_onboarding_role_buttons(sender: str, lang: str, entry_id: int) -> None:
    buttons = [
        {"id": f"onboarding_role_owner_{entry_id}", "title": t("onboarding.btn_owner", lang=lang)},
        {"id": f"onboarding_role_worker_{entry_id}", "title": t("onboarding.btn_worker", lang=lang)},
    ]
    await _send_response_with_buttons(sender, t("onboarding.role_prompt", lang=lang), buttons)


@db_sync_to_async
def _update_waitlist_company_name(entry_id: int, company_name: str) -> None:
    """Update the company name on a waitlist entry."""
    WaitlistEntry.objects.filter(id=entry_id).update(company_name=company_name)


@db_sync_to_async
def _create_waitlist_entry(phone_number: str, first_message: str) -> WaitlistEntry:
    """Create a new waitlist entry."""
    entry, created = WaitlistEntry.objects.get_or_create(
        phone_number=phone_number,
        defaults={"first_message": first_message},
    )
    if not created and not entry.first_message:
        entry.first_message = first_message
        entry.save(update_fields=["first_message"])
    return entry


async def _send_waitlist_admin_notification(entry: WaitlistEntry) -> None:
    """Send a notification to the admin with approve/reject buttons."""
    message = t(
        "waitlist_admin.new_request",
        phone=entry.phone_number,
        message=entry.first_message[:100] if entry.first_message else "(none)",
    )

    buttons = [
        {"id": f"waitlist_approve_{entry.id}", "title": t("waitlist_admin.btn_approve")},
        {"id": f"waitlist_reject_{entry.id}", "title": t("waitlist_admin.btn_reject")},
    ]

    message_sid = await _send_response_with_buttons(
        ADMIN_PHONE_NUMBER,
        message,
        buttons,
    )

    # Store the response message SID for lookup when button is clicked
    if message_sid:
        await _store_waitlist_response_message_sid(entry.id, message_sid)


@db_sync_to_async
def _store_waitlist_response_message_sid(entry_id: int, message_sid: str) -> None:
    """Store the response message SID on the waitlist entry for button click lookup."""
    WaitlistEntry.objects.filter(id=entry_id).update(confirmation_message_sid=message_sid)


async def _create_sale(parsed_items, message_id, company, currency=None):
    """Create sale from parsed items (async wrapper with timing)."""
    async with track("create_sale"):
        @db_sync_to_async
        def _create_sale_sync():
            return create_sale_from_parsed_items(
                items=parsed_items,
                whatsapp_message_id=message_id,
                company=company,
                currency=currency,
            )
        return await _create_sale_sync()


@db_sync_to_async
def _get_sale_items(sale):
    """Get sale items for response (sync wrapper for async context)."""
    items = list(sale.items.select_related("product").all())
    return items


@db_sync_to_async
def _record_inventory_adjustment(company_id: int, product_name: str, quantity_delta: int, notes: str = "") -> bool:
    """Create a stock adjustment for an explicit restock/update message."""
    product = Product.objects.filter(company_id=company_id, name__iexact=product_name).first()
    if not product:
        product = Product.objects.create(company_id=company_id, name=product_name, active=True)

    if quantity_delta == 0:
        return False

    InventoryAdjustment.objects.create(
        product=product,
        quantity_delta=quantity_delta,
        reason=InventoryAdjustment.Reason.PURCHASE if quantity_delta > 0 else InventoryAdjustment.Reason.CORRECTION,
        notes=notes,
    )
    return True


async def _maybe_handle_rule_based_business_message(
    sender: str,
    text: str,
    company: Company | None,
    lang: str,
    message_id: str | None = None,
) -> bool:
    """Handle clear reporting / stock / closing / profit queries before the LLM parser."""
    if not company:
        return False

    rule_result = RULE_BASED_INTENT_PARSER.parse(text or "")
    intent_id = rule_result.intent_id

    if intent_id == "shop.closing":
        closing_time = parse_closing_time_text(text) or parse_closing_time_text(rule_result.slots.time_reference or "")
        if not closing_time:
            await _send_response(sender, "Please send a time like 6pm or 18:00.", reply_to=message_id)
            return True

        await set_company_daily_closing_time(company.id, closing_time, timezone.localdate())
        await _send_response(sender, CLOSING_ACK_TEXT, reply_to=message_id)
        return True

    if intent_id == "report.daily_summary":
        timeframe = rule_result.slots.time_reference or "today"
        summary_request = SimpleNamespace(timeframe=timeframe, product_filter=None)
        await _process_sales_query_message(sender, company, summary_request, text=text, lang=lang)
        return True

    if intent_id == "business.status":
        snapshot = await sync_to_async(build_business_snapshot, thread_sensitive=True)(company)
        await _send_response(sender, format_business_summary(snapshot), reply_to=message_id)
        return True

    if intent_id == "finance.profit_query":
        snapshot = await sync_to_async(build_business_snapshot, thread_sensitive=True)(company)
        await _send_response(sender, format_profit_summary(snapshot), reply_to=message_id)
        return True

    if intent_id == "inventory.update":
        normalized = (text or "").lower()
        if any(marker in normalized for marker in ("add stock", "restock", "refill", "new stock", "delivery", "arrived")):
            qty = rule_result.slots.quantity or 0
            product_name = rule_result.slots.product_name
            if qty and product_name:
                await _record_inventory_adjustment(
                    company.id,
                    product_name,
                    qty,
                    notes=text,
                )
                await _send_response(
                    sender,
                    f"Noted. Added {qty} {product_name} to stock.",
                    reply_to=message_id,
                )
                return True

        snapshot = await sync_to_async(build_business_snapshot, thread_sensitive=True)(company)
        await _send_response(sender, format_low_stock_summary(snapshot), reply_to=message_id)
        return True

    return False


async def _process_message_async(
    message_id: str,
    sender: str,
    text: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Async processing of the incoming message.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        text: The message text
        user_profile: The user profile of the sender
    """
    start_tracking(request_id=message_id)
    try:
        company = user_profile.company if user_profile else None
        lang = user_profile.language if user_profile else DEFAULT_LANGUAGE
        phone_number = _extract_phone_number(sender)

        if company:
            handled_onboarding = await _maybe_handle_known_user_onboarding_step(
                sender=sender,
                text=text,
                user_profile=user_profile,
                lang=lang,
            )
            if handled_onboarding:
                return

            if PRICING_FEATURES_ENABLED:
                if DEBT_CHANGE_RECORDING_ENABLED:
                    pending_action = await _get_pending_action(company.id, phone_number, PENDING_ACTION_RECORD_CHANGE_NAME)
                    if pending_action:
                        handled = await _handle_pending_record_change_name(
                            sender=sender,
                            message_id=message_id,
                            text=text,
                            company_id=company.id,
                            pending_action=pending_action,
                            lang=lang,
                        )
                        if handled:
                            return

                    handled_ledger_query = await _maybe_handle_ledger_query(
                        sender=sender,
                        text=text,
                        company=company,
                        lang=lang,
                    )
                    if handled_ledger_query:
                        return

                    pending_debtor_action = await _get_pending_action(company.id, phone_number, PENDING_ACTION_RECORD_DEBTOR_NAME)
                    if pending_debtor_action:
                        handled = await _handle_pending_record_debtor_name(
                            sender=sender,
                            message_id=message_id,
                            text=text,
                            company_id=company.id,
                            pending_action=pending_debtor_action,
                            lang=lang,
                        )
                        if handled:
                            return

                    handled_short_confirmation = await _maybe_handle_short_confirmation_reply(
                        sender=sender,
                        message_id=message_id,
                        text=text,
                        company=company,
                        lang=lang,
                    )
                    if handled_short_confirmation:
                        return

                handled_management = await _maybe_handle_management_commands(
                    sender=sender,
                    text=text,
                    user_profile=user_profile,
                    lang=lang,
                )
                if handled_management:
                    return

                handled_business = await _maybe_handle_rule_based_business_message(
                    sender=sender,
                    text=text,
                    company=company,
                    lang=lang,
                    message_id=message_id,
                )
                if handled_business:
                    return
        elif classify_confirmation_text(text):
            await _send_response(sender, t("chat.continue_prompt", lang=lang), reply_to=message_id)
            return

        # UNIFIED: Single LLM call for intent + extraction
        try:
            result = await parse_message_unified(text, company=company)
        except Exception as e:
            logger.exception(f"LLM processing failed for message {message_id}: {e}")
            await _send_response(sender, t("error.processing_failed", lang=lang))
            return

        logger.info(f"Parsed message - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, text, user_profile, result)
            return

        if result.intent == "sales_query":
            if not PRICING_FEATURES_ENABLED:
                await _process_sale_message_sales_only(message_id, sender, text, company, result, lang=lang)
                return
            await _process_sales_query_message(sender, company, result, text=text, lang=lang)
            return

        if PRICING_FEATURES_ENABLED:
            tender_amount, _, tender_intent = _extract_tender_amount(
                text,
                company_currency=company.currency if company else None,
            )
            if tender_intent and (
                result.intent == "other"
                or not result.items
                or _is_payment_only_followup(text, result.items, tender_amount, tender_intent)
            ):
                handled = await _maybe_handle_payment_only_message(
                    sender=sender,
                    text=text,
                    company=company,
                    lang=lang,
                )
                if handled:
                    return

        # Default: treat as sale
        if PRICING_FEATURES_ENABLED:
            await _process_sale_message_unified(message_id, sender, text, company, result, lang=lang)
        else:
            await _process_sale_message_sales_only(message_id, sender, text, company, result, lang=lang)
    finally:
        end_tracking()


async def _process_audio_message_async(
    message_id: str,
    sender: str,
    media_id: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Async processing of incoming audio message.

    Downloads audio, transcribes it, and processes the transcription.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        media_id: The Meta media ID
        user_profile: The user profile of the sender
    """
    start_tracking(request_id=message_id)
    try:
        company = user_profile.company if user_profile else None
        lang = user_profile.language if user_profile else DEFAULT_LANGUAGE
        phone_number = _extract_phone_number(sender)

        # Step 1: Download audio from Meta CDN (we need the bytes for both transcription and R2)
        whatsapp_client = get_whatsapp_client()
        media_result = await whatsapp_client.download_media(media_id)

        if not media_result:
            await _send_response(sender, t("audio.download_failed", lang=lang))
            return

        audio_data, mime_type = media_result

        # Step 2: Run transcription and R2 upload IN PARALLEL (both use the same audio_data)
        from services.elevenlabs import ElevenLabsClient, ElevenLabsError

        # Map MIME type to file extension
        mime_to_extension = {
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/aac": "aac",
            "audio/amr": "amr",
        }
        extension = mime_to_extension.get(mime_type, "ogg")
        filename = f"audio.{extension}"

        try:
            elevenlabs_client = ElevenLabsClient()

            # Fire-and-forget R2 upload (archival, not on critical path)
            async def _background_r2_upload():
                """Upload to R2 and update DB in background."""
                r2_url = await _upload_to_r2(audio_data, media_id, mime_type, phone_number)
                if r2_url:
                    @db_sync_to_async
                    def _update_r2_url(msg_id: str, url: str):
                        from apps.whatsapp.models import WhatsAppMessage
                        WhatsAppMessage.objects.filter(whatsapp_message_id=msg_id).update(r2_media_url=url)

                    await _update_r2_url(message_id, r2_url)
                    logger.info(f"R2 upload completed for {message_id}: {r2_url}")

            # Start R2 upload in background (don't await)
            asyncio.create_task(_background_r2_upload())

            # Only await transcription (on critical path). Provide language hint for better Shona quality.
            language_hint = "sn" if lang == "sn" else None
            transcribed_text = await elevenlabs_client.transcribe_audio(
                audio_data,
                filename,
                language_code=language_hint,
            )

            logger.info(f"Transcribed audio message {message_id}: {transcribed_text[:100]}...")

            # Step 3: Update database with transcription (R2 URL updated separately in background)
            @db_sync_to_async
            def _update_message_transcription(msg_id: str, transcript: str):
                from apps.whatsapp.models import WhatsAppMessage
                WhatsAppMessage.objects.filter(whatsapp_message_id=msg_id).update(
                    transcribed_text=transcript,
                    content=transcript,
                )

            await _update_message_transcription(message_id, transcribed_text)

        except ElevenLabsError as e:
            logger.exception(f"Failed to transcribe audio message {message_id}: {e}")
            await _send_response(sender, t("audio.transcription_failed", lang=lang))
            return

        # Step 4: Unified parse (intent + extraction)
        if company:
            if PRICING_FEATURES_ENABLED:
                if DEBT_CHANGE_RECORDING_ENABLED:
                    handled_ledger_query = await _maybe_handle_ledger_query(
                        sender=sender,
                        text=transcribed_text,
                        company=company,
                        lang=lang,
                    )
                    if handled_ledger_query:
                        return

                handled_management = await _maybe_handle_management_commands(
                    sender=sender,
                    text=transcribed_text,
                    user_profile=user_profile,
                    lang=lang,
                )
                if handled_management:
                    return

                handled_business = await _maybe_handle_rule_based_business_message(
                    sender=sender,
                    text=transcribed_text,
                    company=company,
                    lang=lang,
                    message_id=message_id,
                )
                if handled_business:
                    return

        try:
            result = await parse_message_unified(transcribed_text, company=company)
        except Exception as e:
            logger.exception(f"LLM processing failed for audio message {message_id}: {e}")
            await _send_response(sender, t("error.processing_failed", lang=lang))
            return

        logger.info(f"Parsed audio - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, transcribed_text, user_profile, result)
            return

        if result.intent == "sales_query":
            if not PRICING_FEATURES_ENABLED:
                await _process_sale_message_sales_only(message_id, sender, transcribed_text, company, result, is_from_audio=True, lang=lang)
                return
            await _process_sales_query_message(sender, company, result, text=transcribed_text, lang=lang)
            return

        if PRICING_FEATURES_ENABLED:
            tender_amount, _, tender_intent = _extract_tender_amount(
                transcribed_text,
                company_currency=company.currency if company else None,
            )
            if tender_intent and (
                result.intent == "other"
                or not result.items
                or _is_payment_only_followup(transcribed_text, result.items, tender_amount, tender_intent)
            ):
                handled = await _maybe_handle_payment_only_message(
                    sender=sender,
                    text=transcribed_text,
                    company=company,
                    lang=lang,
                )
                if handled:
                    return

        # Default: treat as sale
        if PRICING_FEATURES_ENABLED:
            await _process_sale_message_unified(
                message_id, sender, transcribed_text, company, result, is_from_audio=True, lang=lang
            )
        else:
            await _process_sale_message_sales_only(
                message_id, sender, transcribed_text, company, result, is_from_audio=True, lang=lang
            )
    finally:
        end_tracking()


async def _process_sale_message_sales_only(
    message_id: str,
    sender: str,
    text: str,
    company: "Company | None",
    result,
    is_from_audio: bool = False,
    lang: str = DEFAULT_LANGUAGE,
) -> None:
    """Record sales without exposing any pricing-related behavior."""
    if not company:
        await _send_response(sender, t("sales_query.no_company", lang=lang))
        return

    phone_number = _extract_phone_number(sender)
    draft = await _get_or_create_active_draft(company.id, phone_number, company.currency)

    stripped_items = _strip_pricing_from_items(result.items)
    if not stripped_items:
        await _send_response(sender, t("sale.no_products", lang=lang), reply_to=message_id)
        return

    existing_snapshot = await _get_draft_snapshot(draft.id, company.id)
    should_replace_draft = bool(existing_snapshot["items"]) and _looks_like_fresh_basket_message(text, len(stripped_items))

    if should_replace_draft:
        await _replace_draft_items(draft.id, stripped_items)
    else:
        await _append_items_to_draft(draft.id, stripped_items)

    if not _is_finalize_signal(text):
        snapshot = await _get_draft_snapshot(draft.id, company.id)
        lines = [t("draft.updated", lang=lang)]
        for row in snapshot["items"]:
            lines.append(f"  {row['quantity']}x {row['product_name']}")
        lines.append(t("draft.prompt_more", lang=lang))
        await _send_response(sender, "\n".join(lines), reply_to=message_id)
        return

    draft_snapshot = await _get_draft_snapshot(draft.id, company.id)
    if not draft_snapshot["items"]:
        await _send_response(sender, t("sale.no_products", lang=lang), reply_to=message_id)
        return

    try:
        sale_result = await _finalize_draft_to_sale(
            draft.id,
            message_id,
            company,
            currency=company.currency,
        )
    except PriceOverflowError:
        logger.warning("Price overflow while pricing is disabled for message %s from %s", message_id, sender)
        await _send_response(sender, t("sale.confirmed_ok", lang=lang), reply_to=message_id)
        return

    sale = sale_result["sale"]
    sale_items = await _get_sale_items(sale)
    response_lines = [t("sale.confirmed_ok", lang=lang)]
    for item in sale_items:
        response_lines.append(f"  {item.quantity}x {item.product.name}")
    buttons = [
        {"id": f"confirm_{sale.id}", "title": t("sale.btn_confirm", lang=lang)},
        {"id": f"fix_{sale.id}", "title": t("sale.btn_fix", lang=lang)},
    ]
    message_sid = await _send_response_with_buttons(sender, "\n".join(response_lines), buttons, reply_to=message_id)
    if message_sid:
        await _store_response_message_sid(sale.id, message_sid)


async def _handle_add_assistant(
    sender: str,
    text: str,
    user_profile: UserProfile | None,
    result,  # UnifiedMessageResult
) -> None:
    """Handle a request to add an assistant."""
    lang = user_profile.language if user_profile else DEFAULT_LANGUAGE

    # Check if user is an owner
    if not user_profile or user_profile.role != UserProfile.Role.OWNER:
        await _send_response(sender, t("assistant.not_owner", lang=lang))
        return

    # Get phone number from unified result
    phone_number = result.phone_number
    if not phone_number:
        await _send_response(sender, t("assistant.missing_phone", lang=lang))
        return

    # Check if phone number is already in use
    existing_profile = await _get_profile_by_phone(phone_number)
    if existing_profile:
        await _send_response(
            sender, t("assistant.already_registered", lang=lang, phone=phone_number)
        )
        return

    # Create user and profile for the assistant
    assistant_profile = await _create_assistant(phone_number, user_profile.company)

    # Notify the owner
    await _send_response(
        sender,
        t("assistant.added", lang=lang, phone=phone_number, company=user_profile.company.name),
    )


@db_sync_to_async
def _get_profile_by_phone(phone_number: str) -> UserProfile | None:
    """Get a user profile by phone number."""
    try:
        return UserProfile.objects.select_related("company").get(phone_number=phone_number)
    except UserProfile.DoesNotExist:
        return None


@db_sync_to_async
def _create_assistant(phone_number: str, company) -> UserProfile:
    """Create a new assistant user and profile."""
    # Create user (username from phone, removing non-alphanumeric)
    username = "".join(c for c in phone_number if c.isalnum())

    # Ensure unique username
    base_username = username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{counter}"
        counter += 1

    user = User.objects.create_user(username=username)

    # Create profile as assistant
    profile = UserProfile.objects.create(
        user=user,
        company=company,
        role=UserProfile.Role.ASSISTANT,
        phone_number=phone_number,
    )
    return profile


@db_sync_to_async
def _update_company_currency(company_id: int, currency: str) -> None:
    """Update the company's currency setting."""
    from apps.core.models import Company
    Company.objects.filter(id=company_id).update(currency=currency)


async def _process_sale_message_unified(
    message_id: str,
    sender: str,
    text: str,
    company: "Company | None",
    result,  # UnifiedMessageResult
    is_from_audio: bool = False,
    lang: str = DEFAULT_LANGUAGE,
) -> None:
    """
    Process sale using already-parsed result.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        text: The message text or transcription
        company: The company associated with the sender
        result: The unified parsing result with intent and extracted data
        is_from_audio: Whether this message came from audio transcription
        lang: The user's preferred language
    """
    if not company:
        await _send_response(sender, t("sales_query.no_company", lang=lang))
        return

    # If LLM likely treated tendered cash as unit price, clear unit prices so stored product prices are used.
    tender_amount, tender_currency, tender_intent = _extract_tender_amount(
        text,
        company_currency=company.currency if company else None,
    )
    if tender_amount is not None and _looks_like_tender_not_unit_price(text):
        for item in result.items:
            item["unit_price"] = None

    phone_number = _extract_phone_number(sender)
    draft_currency = result.currency or company.currency
    draft = await _get_or_create_active_draft(company.id, phone_number, draft_currency)
    should_finalize = bool(result.items) or (tender_amount is not None and tender_intent) or _is_finalize_signal(text)
    payment_only_followup = _is_payment_only_followup(text, result.items, tender_amount, tender_intent)
    has_inline_checkout = bool(result.items) and tender_amount is not None and tender_intent and not payment_only_followup
    existing_snapshot = await _get_draft_snapshot(draft.id, company.id)

    if not result.items and not should_finalize:
        # No sale items and no finalize signal/payment intent.
        response = t("sale.parse_failed", lang=lang)
        await _send_response(sender, response)
        return

    updated_products: list[str] = []
    if _looks_like_price_update_message(text):
        updated_products = await _apply_price_updates_to_draft(draft.id, result.items)

    items_to_append = result.items
    if updated_products:
        updated_set = {p.lower() for p in updated_products}
        # Avoid double counting when the message was only clarifying missing prices.
        items_to_append = [
            item for item in result.items if (item.get("product_name") or "").strip().lower() not in updated_set
        ]

    should_replace_draft = bool(existing_snapshot["items"]) and _looks_like_fresh_basket_message(text, len(items_to_append))

    if items_to_append and not payment_only_followup:
        if has_inline_checkout:
            # Treat "items + paid amount" as a one-shot checkout for this message.
            # This prevents stale draft lines from leaking into the current calculation.
            await _replace_draft_items(draft.id, items_to_append)
        elif should_replace_draft:
            await _replace_draft_items(draft.id, items_to_append)
        else:
            await _append_items_to_draft(draft.id, items_to_append)

    if not should_finalize:
        snapshot = await _get_draft_snapshot(draft.id, company.id)
        lines = [t("draft.updated", lang=lang)]
        if updated_products:
            for product_name in sorted(set(updated_products)):
                lines.append(t("draft.price_updated", lang=lang, product=product_name))
        unknown_price_products: list[str] = []
        for row in snapshot["items"]:
            if snapshot["currency"] and row["unit_price"] is not None:
                lines.append(
                    t(
                        "draft.item_with_price",
                        lang=lang,
                        quantity=row["quantity"],
                        product=row["product_name"],
                        price=format_price(row["unit_price"], snapshot["currency"]),
                    )
                )
            elif snapshot["currency"] and row.get("declared_total_amount") is not None:
                lines.append(
                    t(
                        "draft.item_total",
                        lang=lang,
                        quantity=row["quantity"],
                        product=row["product_name"],
                        total=format_price(row["declared_total_amount"], snapshot["currency"]),
                    )
                )
            else:
                lines.append(
                    t(
                        "draft.item_no_price",
                        lang=lang,
                        quantity=row["quantity"],
                        product=row["product_name"],
                    )
                )
                unknown_price_products.append(row["product_name"])

        if snapshot["all_priced"] and snapshot["currency"]:
            lines.append(
                t(
                    "draft.total_estimated",
                    lang=lang,
                    total=format_price(snapshot["estimated_total"], snapshot["currency"]),
                )
            )

        if unknown_price_products:
            for product_name in sorted(set(unknown_price_products)):
                lines.append(t("draft.unknown_price_item", lang=lang, product=product_name))
        lines.append(t("draft.prompt_more", lang=lang))
        await _send_response(sender, "\n".join(lines), reply_to=message_id)
        return

    draft_snapshot = await _get_draft_snapshot(draft.id, company.id)
    if not draft_snapshot["items"]:
        await _send_response(sender, t("sale.no_products", lang=lang), reply_to=message_id)
        return

    # Update company currency if detected
    if result.currency and company:
        await _update_company_currency(company.id, result.currency)
        company.currency = result.currency

    # Create sale from draft basket
    try:
        sale_result = await _finalize_draft_to_sale(
            draft.id,
            message_id,
            company,
            currency=result.currency or company.currency,
        )
    except PriceOverflowError:
        logger.warning("Price overflow for message %s from %s", message_id, sender)
        response = t("sale.price_too_large", lang=lang)
        await _send_response(sender, response, reply_to=message_id)
        return
    sale = sale_result["sale"]
    unmatched = sale_result["unmatched_items"]

    # Build response message
    response_lines = [t("sale.receipt_header", lang=lang)]

    sale_items = await _get_sale_items(sale)
    has_missing_prices = False
    currencies_in_sale = set()
    unknown_price_products: list[str] = []

    if sale_items:
        for item in sale_items:
            if item.unit_price is not None and item.currency:
                item_currency = item.currency
                currencies_in_sale.add(item_currency)
                response_lines.append(t("sale.item_with_price", lang=lang, quantity=item.quantity, product=item.product.name, price=format_price(item.unit_price, item_currency)))
            elif item.declared_total_amount is not None and item.currency:
                item_currency = item.currency
                currencies_in_sale.add(item_currency)
                response_lines.append(t("sale.item_total", lang=lang, quantity=item.quantity, product=item.product.name, total=format_price(item.declared_total_amount, item_currency)))
            else:
                response_lines.append(t("sale.item_no_price", lang=lang, quantity=item.quantity, product=item.product.name))
                has_missing_prices = True
                unknown_price_products.append(item.product.name)

        sale_currency = None
        if len(currencies_in_sale) == 1:
            sale_currency = currencies_in_sale.pop()
            response_lines.append(t("sale.total", lang=lang, total=format_price(sale.total_amount, sale_currency)))

        # Payment intelligence: if message contains tendered amount, calculate change or shortfall.
        # This now runs for any single-currency sale, even when some lines were missing explicit prices.
        if sale_currency and tender_amount is not None and tender_intent:
            effective_tender_currency = tender_currency or sale_currency
            if effective_tender_currency == sale_currency:
                delta = tender_amount - sale.total_amount
                if delta > 0:
                    response_lines.append(
                        t(
                            "payment.change_due",
                            lang=lang,
                            paid=format_price(tender_amount, sale_currency),
                            change=format_price(delta, sale_currency),
                        )
                    )
                    response_lines.append(t("payment.delta_not_recorded", lang=lang))
                elif delta < 0:
                    remaining_needed = abs(delta)
                    response_lines.append(
                        t(
                            "payment.shortfall",
                            lang=lang,
                            paid=format_price(tender_amount, sale_currency),
                            needed=format_price(remaining_needed, sale_currency),
                        )
                    )
                    response_lines.append(t("payment.delta_not_recorded", lang=lang))
                else:
                    response_lines.append(
                        t(
                            "payment.paid_exact",
                            lang=lang,
                            paid=format_price(tender_amount, sale_currency),
                        )
                    )

        if has_missing_prices:
            response_lines.append(t("sale.missing_prices_note", lang=lang))

    if unknown_price_products:
        for product_name in sorted(set(unknown_price_products)):
            response_lines.append(t("sale.unknown_price_item", lang=lang, product=product_name))

    if unmatched:
        response_lines.append(t("sale.unmatched", lang=lang, items=", ".join(unmatched)))

    # Send with confirmation buttons
    buttons = []
    buttons.extend(
        [
            {"id": f"confirm_{sale.id}", "title": t("sale.btn_confirm", lang=lang)},
            {"id": f"fix_{sale.id}", "title": t("sale.btn_fix", lang=lang)},
        ]
    )
    message_sid = await _send_response_with_buttons(
        sender, "\n".join(response_lines), buttons, reply_to=message_id
    )

    # Store the response message SID for lookup when button is clicked
    if message_sid:
        await _store_response_message_sid(sale.id, message_sid)


async def _send_response(to: str, message: str, reply_to: str | None = None) -> None:
    """Send a response message back to the sender."""
    client = get_whatsapp_client()
    await client.send_message(to, message, reply_to=reply_to)


async def _send_response_with_buttons(
    to: str, message: str, buttons: list[dict[str, str]], reply_to: str | None = None
) -> str | None:
    """Send a response message with buttons back to the sender."""
    client = get_whatsapp_client()
    return await client.send_message_with_buttons(to, message, buttons, reply_to=reply_to)


async def _send_typing_indicator(to: str, message_id: str | None = None) -> None:
    """Send typing indicator without interrupting normal message handling."""
    try:
        client = get_whatsapp_client()
        send_indicator = getattr(client, "send_typing_indicator", None)
        if callable(send_indicator):
            await send_indicator(to, message_id=message_id)
    except Exception:
        logger.debug("Typing indicator failed", exc_info=False)


async def _maybe_handle_payment_only_message(
    sender: str,
    text: str,
    company: "Company | None",
    lang: str = DEFAULT_LANGUAGE,
) -> bool:
    """Handle messages like 'i was given $20' by applying to the most recent sale."""
    if not company:
        return False

    tender_amount, tender_currency, has_payment_intent = _extract_tender_amount(
        text,
        company_currency=company.currency,
    )
    if tender_amount is None or not has_payment_intent:
        return False

    phone_number = _extract_phone_number(sender)

    # First priority: if an active draft exists, finalize it with this payment.
    draft = await _get_active_draft(company.id, phone_number)
    if draft:
        snapshot = await _get_draft_snapshot(draft.id, company.id)
        if not snapshot["items"]:
            return False

        try:
            sale_result = await _finalize_draft_to_sale(
                draft.id,
                None,
                company,
                currency=snapshot["currency"] or company.currency,
            )
        except PriceOverflowError:
            await _send_response(sender, t("sale.price_too_large", lang=lang))
            return True

        sale = sale_result["sale"]
        sale_items = await _get_sale_items(sale)
        sale_currencies = {i.currency for i in sale_items if i.currency}

        lines = [t("sale.receipt_header", lang=lang)]
        for item in sale_items:
            if item.unit_price is not None and item.currency:
                lines.append(
                    t(
                        "sale.item_with_price",
                        lang=lang,
                        quantity=item.quantity,
                        product=item.product.name,
                        price=format_price(item.unit_price, item.currency),
                    )
                )
            elif item.declared_total_amount is not None and item.currency:
                lines.append(
                    t(
                        "sale.item_total",
                        lang=lang,
                        quantity=item.quantity,
                        product=item.product.name,
                        total=format_price(item.declared_total_amount, item.currency),
                    )
                )
            else:
                lines.append(t("sale.item_no_price", lang=lang, quantity=item.quantity, product=item.product.name))

        sale_currency = None
        if len(sale_currencies) == 1:
            sale_currency = next(iter(sale_currencies))
            lines.append(t("sale.total", lang=lang, total=format_price(sale.total_amount, sale_currency)))

        if sale_currency:
            effective_tender_currency = tender_currency or sale_currency
            if effective_tender_currency == sale_currency:
                delta = tender_amount - sale.total_amount
                if delta > 0:
                    lines.append(
                        t(
                            "payment.change_due",
                            lang=lang,
                            paid=format_price(tender_amount, sale_currency),
                            change=format_price(delta, sale_currency),
                        )
                    )
                    lines.append(t("payment.delta_not_recorded", lang=lang))
                elif delta < 0:
                    remaining = abs(delta)
                    lines.append(
                        t(
                            "payment.shortfall",
                            lang=lang,
                            paid=format_price(tender_amount, sale_currency),
                            needed=format_price(remaining, sale_currency),
                        )
                    )
                    lines.append(t("payment.delta_not_recorded", lang=lang))
                else:
                    lines.append(
                        t(
                            "payment.paid_exact",
                            lang=lang,
                            paid=format_price(tender_amount, sale_currency),
                        )
                    )

        buttons = []
        buttons.extend(
            [
                {"id": f"confirm_{sale.id}", "title": t("sale.btn_confirm", lang=lang)},
                {"id": f"fix_{sale.id}", "title": t("sale.btn_fix", lang=lang)},
            ]
        )

        message_sid = await _send_response_with_buttons(sender, "\n".join(lines), buttons)
        if message_sid:
            await _store_response_message_sid(sale.id, message_sid)
        else:
            await _send_response(sender, "\n".join(lines))
        return True

    sale = await _get_most_recent_confirmed_sale(company.id)
    if not sale:
        await _send_response(sender, t("payment.no_recent_sale", lang=lang))
        return True

    sale_items = await _get_sale_items(sale)
    sale_currencies = {i.currency for i in sale_items if i.currency}
    if len(sale_currencies) != 1:
        await _send_response(sender, t("payment.multiple_currency_recent", lang=lang))
        return True

    sale_currency = next(iter(sale_currencies), company.currency or "USD")
    effective_tender_currency = tender_currency or sale_currency
    if effective_tender_currency != sale_currency:
        await _send_response(
            sender,
            t(
                "payment.currency_mismatch",
                lang=lang,
                sale_currency=sale_currency,
                paid_currency=effective_tender_currency,
            ),
        )
        return True

    delta = tender_amount - sale.total_amount
    lines = [t("sale.receipt_header", lang=lang)]
    for item in sale_items:
        if item.unit_price is not None and item.currency:
            lines.append(
                t(
                    "sale.item_with_price",
                    lang=lang,
                    quantity=item.quantity,
                    product=item.product.name,
                    price=format_price(item.unit_price, item.currency),
                )
            )
        elif item.declared_total_amount is not None and item.currency:
            lines.append(
                t(
                    "sale.item_total",
                    lang=lang,
                    quantity=item.quantity,
                    product=item.product.name,
                    total=format_price(item.declared_total_amount, item.currency),
                )
            )
        else:
            lines.append(t("sale.item_no_price", lang=lang, quantity=item.quantity, product=item.product.name))
    lines.append(t("sale.total", lang=lang, total=format_price(sale.total_amount, sale_currency)))
    if delta > 0:
        lines.append(
            t(
                "payment.change_due",
                lang=lang,
                paid=format_price(tender_amount, sale_currency),
                change=format_price(delta, sale_currency),
            )
        )
        lines.append(t("payment.delta_not_recorded", lang=lang))
    elif delta < 0:
        remaining_needed = abs(delta)
        lines.append(
            t(
                "payment.shortfall",
                lang=lang,
                paid=format_price(tender_amount, sale_currency),
                needed=format_price(remaining_needed, sale_currency),
            )
        )
        lines.append(t("payment.delta_not_recorded", lang=lang))
    else:
        lines.append(t("payment.paid_exact", lang=lang, paid=format_price(tender_amount, sale_currency)))

    buttons = []
    buttons.extend(
        [
            {"id": f"confirm_{sale.id}", "title": t("sale.btn_confirm", lang=lang)},
            {"id": f"fix_{sale.id}", "title": t("sale.btn_fix", lang=lang)},
        ]
    )

    message_sid = await _send_response_with_buttons(sender, "\n".join(lines), buttons)
    if message_sid:
        await _store_response_message_sid(sale.id, message_sid)
    else:
        await _send_response(sender, "\n".join(lines))
    return True


def _detect_language_switch_target(text: str) -> str | None:
    """Detect explicit language switch commands for approved users.

    Returns:
        "en", "sn", or None.
    """
    normalized = _normalize_message_for_matching(text)
    if not normalized:
        return None

    if normalized in {"english", "en"}:
        return "en"
    if normalized in {"shona", "sn"}:
        return "sn"

    # English switch commands, e.g. "switch to english", "change language shona".
    english_switch = re.search(
        r"\b(?:switch|change|set)\b[^\n]{0,24}\b(?:english|en)\b",
        normalized,
        re.IGNORECASE,
    )
    shona_switch = re.search(
        r"\b(?:switch|change|set)\b[^\n]{0,24}\b(?:shona|sn)\b",
        normalized,
        re.IGNORECASE,
    )
    if english_switch:
        return "en"
    if shona_switch:
        return "sn"

    # Shona variants, e.g. "chinja mutauro ku shona".
    if re.search(r"\b(?:mutauro|chinja)\b[^\n]{0,24}\b(?:english|en)\b", normalized, re.IGNORECASE):
        return "en"
    if re.search(r"\b(?:mutauro|chinja)\b[^\n]{0,24}\b(?:shona|sn)\b", normalized, re.IGNORECASE):
        return "sn"

    return None


def _is_help_command(text: str) -> bool:
    """Detect lightweight help command variants."""
    normalized = _normalize_message_for_matching(text)
    return normalized in {"help", "?", "menu", "rubatsiro"}


async def _maybe_handle_management_commands(
    sender: str,
    text: str,
    user_profile: UserProfile | None,
    lang: str,
) -> bool:
    """Handle language switch and sale edit/delete commands."""
    if not user_profile or not user_profile.company_id:
        return False

    if _is_help_command(text):
        await _send_response(sender, t("help.menu", lang=lang))
        return True

    # Language switch commands
    target_lang = _detect_language_switch_target(text)
    if target_lang and target_lang in ("en", "sn"):
        if user_profile.language != target_lang:
            await _update_profile_language(user_profile.id, target_lang)
            user_profile.language = target_lang
        await _send_response(
            sender,
            t("language.changed_anytime", lang=target_lang, selected=("English" if target_lang == "en" else "Shona")),
        )
        return True

    command = _extract_management_command(text)
    if not command:
        return False

    company_id = user_profile.company_id
    sale = await _get_sale_for_company(
        company_id=company_id,
        sale_id=None if command.get("use_last") else command.get("sale_id"),
    )
    if not sale:
        await _send_response(sender, t("management.sale_not_found", lang=lang))
        return True

    if command["action"] == "delete":
        deleted = await _delete_sale_for_company(company_id, sale.id)
        if deleted:
            await _send_response(sender, t("management.sale_deleted", lang=lang, sale_id=sale.id))
        else:
            await _send_response(sender, t("management.sale_not_found", lang=lang))
        return True

    # Edit flow
    new_text = (command.get("new_text") or "").strip()
    if not new_text:
        await _send_response(sender, t("management.edit_usage", lang=lang))
        return True

    try:
        parsed = await parse_message_unified(new_text, company=user_profile.company)
    except Exception:
        await _send_response(sender, t("error.processing_failed", lang=lang))
        return True

    if parsed.intent != "sale" or not parsed.items:
        await _send_response(sender, t("management.edit_invalid", lang=lang))
        return True

    parsed_items = [
        {
            "product_name": row.product_name,
            "quantity": row.quantity,
            "unit_price": row.unit_price,
        }
        for row in parsed.items
    ]
    ok = await _replace_sale_items_from_parsed(
        company_id=company_id,
        sale_id=sale.id,
        parsed_items=parsed_items,
        currency=parsed.currency or user_profile.company.currency,
    )
    if not ok:
        await _send_response(sender, t("management.sale_not_found", lang=lang))
        return True

    await _send_response(sender, t("management.sale_updated", lang=lang, sale_id=sale.id))
    return True


@db_sync_to_async
def _get_most_recent_confirmed_sale(company_id: int) -> Sale | None:
    """Fetch the latest confirmed sale for a company in the recent window."""
    recent_threshold = timezone.now() - timedelta(minutes=20)
    return (
        Sale.objects.filter(
            company_id=company_id,
            status=Sale.Status.CONFIRMED,
            sale_timestamp__gte=recent_threshold,
        )
        .order_by("-sale_timestamp")
        .first()
    )


@db_sync_to_async
def _get_sale_for_company(company_id: int, sale_id: int | None = None) -> Sale | None:
    queryset = Sale.objects.filter(company_id=company_id, status=Sale.Status.CONFIRMED).order_by("-sale_timestamp")
    if sale_id is not None:
        queryset = queryset.filter(id=sale_id)
    return queryset.first()


@db_sync_to_async
def _delete_sale_for_company(company_id: int, sale_id: int) -> bool:
    deleted, _ = Sale.objects.filter(company_id=company_id, id=sale_id).delete()
    return deleted > 0


@db_sync_to_async
def _replace_sale_items_from_parsed(
    company_id: int,
    sale_id: int,
    parsed_items: list[dict],
    currency: str | None,
) -> bool:
    sale = Sale.objects.filter(company_id=company_id, id=sale_id).select_related("company").first()
    if not sale or not parsed_items:
        return False

    with transaction.atomic():
        sale.items.all().delete()
        default_currency = currency or (sale.company.currency if sale.company else "USD")

        created_any = False
        for row in parsed_items:
            product_name = (row.get("product_name") or "").strip()
            if not product_name:
                continue

            try:
                quantity = int(row.get("quantity") or 1)
            except (TypeError, ValueError):
                quantity = 1

            unit_price = row.get("unit_price")
            product = Product.objects.filter(company_id=company_id, name__iexact=product_name).first()
            if not product:
                product = Product.objects.create(company_id=company_id, name=product_name, active=True)

            SaleItem.objects.create(
                sale_id=sale.id,
                product_id=product.id,
                quantity=max(quantity, 1),
                unit_price=unit_price,
                declared_total_amount=row.get("declared_total_amount"),
                currency=default_currency,
            )
            created_any = True

        if not created_any:
            transaction.set_rollback(True)
            return False

        sale.save()  # Recalculate total
    return True


@db_sync_to_async
def _store_response_message_sid(sale_id: int, message_sid: str) -> None:
    """Store the response message SID on the sale for button click lookup."""
    Sale.objects.filter(id=sale_id).update(confirmation_message_sid=message_sid)


@db_sync_to_async
def _get_or_create_active_draft(company_id: int, phone_number: str, currency: str | None = None) -> DraftSale:
    draft = (
        DraftSale.objects.filter(company_id=company_id, phone_number=phone_number, active=True)
        .order_by("-updated_at")
        .first()
    )
    if draft:
        if currency and not draft.currency:
            draft.currency = currency
            draft.save(update_fields=["currency", "updated_at"])
        return draft

    return DraftSale.objects.create(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
        active=True,
    )


@db_sync_to_async
def _get_active_draft(company_id: int, phone_number: str) -> DraftSale | None:
    return (
        DraftSale.objects.filter(company_id=company_id, phone_number=phone_number, active=True)
        .order_by("-updated_at")
        .first()
    )


@db_sync_to_async
def _append_items_to_draft(draft_id: int, items: list[dict]) -> None:
    draft = DraftSale.objects.get(id=draft_id)
    for item in items:
        name = (item.get("product_name") or "").strip()
        if not name:
            continue
        qty = int(item.get("quantity") or 1)
        unit_price = item.get("unit_price")
        declared_total_amount = item.get("declared_total_amount")

        existing = DraftSaleItem.objects.filter(
            draft_sale=draft,
            product_name__iexact=name,
            unit_price=unit_price,
            declared_total_amount=declared_total_amount,
        ).first()
        if existing:
            existing.quantity += qty
            existing.save(update_fields=["quantity"])
        else:
            DraftSaleItem.objects.create(
                draft_sale=draft,
                product_name=name,
                quantity=qty,
                unit_price=unit_price,
                declared_total_amount=declared_total_amount,
            )
    draft.save(update_fields=["updated_at"])


@db_sync_to_async
def _replace_draft_items(draft_id: int, items: list[dict]) -> None:
    draft = DraftSale.objects.get(id=draft_id)
    draft.items.all().delete()
    for item in items:
        name = (item.get("product_name") or "").strip()
        if not name:
            continue
        DraftSaleItem.objects.create(
            draft_sale=draft,
            product_name=name,
            quantity=int(item.get("quantity") or 1),
            unit_price=item.get("unit_price"),
            declared_total_amount=item.get("declared_total_amount"),
        )
    draft.save(update_fields=["updated_at"])


@db_sync_to_async
def _apply_price_updates_to_draft(draft_id: int, items: list[dict]) -> list[str]:
    """Apply incoming unit prices to existing draft items that still have unknown prices.

    Returns a list of product names that were updated.
    """
    draft = DraftSale.objects.get(id=draft_id)
    updated_products: list[str] = []

    for item in items:
        name = (item.get("product_name") or "").strip()
        unit_price = item.get("unit_price")
        if not name or unit_price is None:
            continue

        unknown_rows = DraftSaleItem.objects.filter(
            draft_sale=draft,
            product_name__iexact=name,
            unit_price__isnull=True,
            declared_total_amount__isnull=True,
        )
        if not unknown_rows.exists():
            continue

        unknown_rows.update(unit_price=unit_price)
        updated_products.append(name)

    if updated_products:
        draft.save(update_fields=["updated_at"])
    return updated_products


@db_sync_to_async
def _get_draft_snapshot(draft_id: int, company_id: int) -> dict:
    from apps.catalog.models import Product
    draft = DraftSale.objects.get(id=draft_id)
    rows = []
    estimated_total = Decimal("0.00")
    all_priced = True
    currency = draft.currency

    for row in draft.items.all():
        unit_price = row.unit_price
        declared_total_amount = row.declared_total_amount

        line_total = None
        if declared_total_amount is not None:
            line_total = Decimal(str(declared_total_amount))
            estimated_total += line_total
        elif unit_price is not None:
            line_total = Decimal(str(row.quantity)) * Decimal(str(unit_price))
            estimated_total += line_total
        else:
            all_priced = False

        rows.append(
            {
                "product_name": row.product_name,
                "quantity": row.quantity,
                "unit_price": unit_price,
                "declared_total_amount": declared_total_amount,
                "line_total": line_total,
            }
        )

    return {
        "items": rows,
        "all_priced": all_priced,
        "estimated_total": estimated_total,
        "currency": currency,
    }


@db_sync_to_async
def _finalize_draft_to_sale(
    draft_id: int,
    message_id: str | None,
    company,
    currency: str | None,
) -> dict:
    draft = DraftSale.objects.get(id=draft_id)
    parsed_items = []
    for row in draft.items.all():
        parsed_items.append(
            {
                "product_name": row.product_name,
                "quantity": row.quantity,
                "unit_price": row.unit_price,
                "declared_total_amount": row.declared_total_amount,
            }
        )

    result = create_sale_from_parsed_items(
        items=parsed_items,
        whatsapp_message_id=message_id,
        company=company,
        currency=currency or draft.currency,
    )

    draft.active = False
    draft.save(update_fields=["active", "updated_at"])
    draft.items.all().delete()
    return result


@db_sync_to_async
def _get_credit_balance(company_id: int, phone_number: str, currency: str) -> Decimal:
    record = CustomerCreditBalance.objects.filter(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
    ).first()
    if not record:
        return Decimal("0.00")
    return Decimal(str(record.balance))


@db_sync_to_async
def _add_credit(company_id: int, phone_number: str, currency: str, amount: Decimal, sale_id: int | None = None) -> None:
    if amount <= 0:
        return
    record, _ = CustomerCreditBalance.objects.get_or_create(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
        defaults={"balance": Decimal("0.00")},
    )
    record.balance = Decimal(str(record.balance)) + amount
    record.save(update_fields=["balance", "updated_at"])
    CustomerCreditTransaction.objects.create(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
        amount=amount,
        kind=CustomerCreditTransaction.Kind.CREDIT_ADDED,
        sale_id=sale_id,
        note="Overpayment stored as credit",
    )


@db_sync_to_async
def _consume_credit(company_id: int, phone_number: str, currency: str, required: Decimal, sale_id: int | None = None) -> Decimal:
    if required <= 0:
        return Decimal("0.00")
    record = CustomerCreditBalance.objects.filter(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
    ).first()
    if not record or Decimal(str(record.balance)) <= 0:
        return Decimal("0.00")

    available = Decimal(str(record.balance))
    used = min(available, required)
    record.balance = available - used
    record.save(update_fields=["balance", "updated_at"])
    CustomerCreditTransaction.objects.create(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
        amount=used,
        kind=CustomerCreditTransaction.Kind.CREDIT_USED,
        sale_id=sale_id,
        note="Credit applied to shortfall",
    )
    return used


@db_sync_to_async
def _record_debt(company_id: int, phone_number: str, currency: str, amount: Decimal, sale_id: int | None = None) -> None:
    """Persist unpaid shortfall as debtor balance (negative credit)."""
    if amount <= 0:
        return
    record, _ = CustomerCreditBalance.objects.get_or_create(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
        defaults={"balance": Decimal("0.00")},
    )
    record.balance = Decimal(str(record.balance)) - amount
    record.save(update_fields=["balance", "updated_at"])
    CustomerCreditTransaction.objects.create(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
        amount=amount,
        kind=CustomerCreditTransaction.Kind.CREDIT_USED,
        sale_id=sale_id,
        note="Debt recorded from unpaid shortfall",
    )


@db_sync_to_async
def _get_pending_action(company_id: int, phone_number: str, action_type: str) -> dict | None:
    pending = PendingAction.objects.filter(
        company_id=company_id,
        phone_number=phone_number,
        action_type=action_type,
    ).first()
    if not pending:
        return None
    return {
        "id": pending.id,
        "payload": pending.payload or {},
    }


@db_sync_to_async
def _set_pending_record_change_name(
    company_id: int,
    phone_number: str,
    sale_id: int,
    amount: Decimal,
    currency: str,
) -> None:
    PendingAction.objects.update_or_create(
        company_id=company_id,
        phone_number=phone_number,
        action_type=PENDING_ACTION_RECORD_CHANGE_NAME,
        defaults={
            "payload": {
                "sale_id": sale_id,
                "amount": str(amount),
                "currency": currency,
            }
        },
    )


@db_sync_to_async
def _set_pending_record_debtor_name(
    company_id: int,
    phone_number: str,
    sale_id: int,
    amount: Decimal,
    currency: str,
) -> dict:
    pending, _ = PendingAction.objects.update_or_create(
        company_id=company_id,
        phone_number=phone_number,
        action_type=PENDING_ACTION_RECORD_DEBTOR_NAME,
        defaults={
            "payload": {
                "sale_id": sale_id,
                "amount": str(amount),
                "currency": currency,
            }
        },
    )
    return {"id": pending.id, "payload": pending.payload or {}}


@db_sync_to_async
def _set_pending_remove_debt_confirm(
    company_id: int,
    phone_number: str,
    debtor_name: str,
    timeframe: str,
) -> dict:
    pending, _ = PendingAction.objects.update_or_create(
        company_id=company_id,
        phone_number=phone_number,
        action_type=PENDING_ACTION_REMOVE_DEBT_CONFIRM,
        defaults={
            "payload": {
                "debtor_name": debtor_name,
                "timeframe": timeframe,
            }
        },
    )
    return {"id": pending.id, "payload": pending.payload or {}}


@db_sync_to_async
def _set_pending_remove_change_confirm(
    company_id: int,
    phone_number: str,
    customer_name: str,
    timeframe: str,
) -> dict:
    pending, _ = PendingAction.objects.update_or_create(
        company_id=company_id,
        phone_number=phone_number,
        action_type=PENDING_ACTION_REMOVE_CHANGE_CONFIRM,
        defaults={
            "payload": {
                "customer_name": customer_name,
                "timeframe": timeframe,
            }
        },
    )
    return {"id": pending.id, "payload": pending.payload or {}}


@db_sync_to_async
def _remove_debtor_records_for_pending(company_id: int, payload: dict) -> dict:
    debtor_name = payload.get("debtor_name")
    timeframe = payload.get("timeframe") or "all_time"
    if not debtor_name:
        return {"deleted_count": 0, "debtor_name": ""}

    queryset = DebtorRecord.objects.filter(
        company_id=company_id,
        debtor_name__iexact=debtor_name,
    )
    if timeframe != "all_time":
        start_date, end_date = _get_date_range(timeframe)
        queryset = queryset.filter(recorded_at__gte=start_date, recorded_at__lte=end_date)

    deleted_count = queryset.count()
    if deleted_count > 0:
        queryset.delete()

    return {"deleted_count": deleted_count, "debtor_name": debtor_name}


@db_sync_to_async
def _remove_change_records_for_pending(company_id: int, payload: dict) -> dict:
    customer_name = payload.get("customer_name")
    timeframe = payload.get("timeframe") or "all_time"
    if not customer_name:
        return {"deleted_count": 0, "customer_name": ""}

    queryset = ChangeRecord.objects.filter(
        company_id=company_id,
        customer_name__iexact=customer_name,
    )
    if timeframe != "all_time":
        start_date, end_date = _get_date_range(timeframe)
        queryset = queryset.filter(recorded_at__gte=start_date, recorded_at__lte=end_date)

    deleted_count = queryset.count()
    if deleted_count > 0:
        queryset.delete()

    return {"deleted_count": deleted_count, "customer_name": customer_name}


@db_sync_to_async
def _clear_pending_action(pending_action_id: int) -> None:
    PendingAction.objects.filter(id=pending_action_id).delete()


@db_sync_to_async
def _get_sale_change_info(company_id: int, sale_id: int) -> dict | None:
    sale = Sale.objects.filter(id=sale_id, company_id=company_id).first()
    if not sale:
        return None

    transaction = (
        CustomerCreditTransaction.objects.filter(
            sale_id=sale_id,
            company_id=company_id,
            kind=CustomerCreditTransaction.Kind.CREDIT_ADDED,
            amount__gt=Decimal("0.00"),
        )
        .order_by("-created_at")
        .first()
    )
    if not transaction:
        return None

    return {
        "sale_id": sale.id,
        "amount": Decimal(str(transaction.amount)),
        "currency": transaction.currency,
    }


@db_sync_to_async
def _create_change_record(
    company_id: int,
    phone_number: str,
    sale_id: int,
    customer_name: str,
    amount: Decimal,
    currency: str,
    recorded_by_message_id: str,
) -> None:
    ChangeRecord.objects.create(
        company_id=company_id,
        phone_number=phone_number,
        sale_id=sale_id,
        customer_name=customer_name,
        currency=currency,
        amount=amount,
        recorded_by_message_id=recorded_by_message_id,
    )


@db_sync_to_async
def _create_debtor_record(
    company_id: int,
    phone_number: str,
    sale_id: int,
    debtor_name: str,
    amount: Decimal,
    currency: str,
    recorded_by_message_id: str,
) -> None:
    DebtorRecord.objects.create(
        company_id=company_id,
        phone_number=phone_number,
        sale_id=sale_id,
        debtor_name=debtor_name,
        currency=currency,
        amount=amount,
        recorded_by_message_id=recorded_by_message_id,
    )


async def _handle_pending_record_change_name(
    sender: str,
    message_id: str,
    text: str,
    company_id: int,
    pending_action: dict,
    lang: str,
) -> bool:
    customer_name = (text or "").strip()
    if not _looks_like_customer_name(customer_name):
        await _send_response(sender, t("payment.change_name_prompt", lang=lang), reply_to=message_id)
        return True

    payload = pending_action.get("payload") or {}
    sale_id = payload.get("sale_id")
    amount_raw = payload.get("amount")
    currency = payload.get("currency")

    if not sale_id or not amount_raw or not currency:
        await _clear_pending_action(pending_action["id"])
        await _send_response(sender, t("error.processing_failed", lang=lang), reply_to=message_id)
        return True

    amount = Decimal(str(amount_raw))
    phone_number = _extract_phone_number(sender)
    await _create_change_record(
        company_id=company_id,
        phone_number=phone_number,
        sale_id=int(sale_id),
        customer_name=customer_name,
        amount=amount,
        currency=currency,
        recorded_by_message_id=message_id,
    )
    await _clear_pending_action(pending_action["id"])

    await _send_response(
        sender,
        t(
            "payment.change_recorded",
            lang=lang,
            customer_name=customer_name,
            amount=format_price(amount, currency),
        ),
        reply_to=message_id,
    )
    return True


async def _handle_pending_record_debtor_name(
    sender: str,
    message_id: str,
    text: str,
    company_id: int,
    pending_action: dict,
    lang: str,
) -> bool:
    debtor_name = (text or "").strip()
    if not _looks_like_customer_name(debtor_name):
        await _send_response(sender, t("payment.debtor_name_prompt", lang=lang), reply_to=message_id)
        return True

    payload = pending_action.get("payload") or {}
    sale_id = payload.get("sale_id")
    amount_raw = payload.get("amount")
    currency = payload.get("currency")

    if not sale_id or not amount_raw or not currency:
        await _clear_pending_action(pending_action["id"])
        await _send_response(sender, t("error.processing_failed", lang=lang), reply_to=message_id)
        return True

    amount = Decimal(str(amount_raw))
    phone_number = _extract_phone_number(sender)

    await _record_debt(
        company_id=company_id,
        phone_number=phone_number,
        currency=currency,
        amount=amount,
        sale_id=int(sale_id),
    )
    await _create_debtor_record(
        company_id=company_id,
        phone_number=phone_number,
        sale_id=int(sale_id),
        debtor_name=debtor_name,
        amount=amount,
        currency=currency,
        recorded_by_message_id=message_id,
    )
    await _clear_pending_action(pending_action["id"])

    await _send_response(
        sender,
        t(
            "payment.debtor_recorded",
            lang=lang,
            debtor_name=debtor_name,
            amount=format_price(amount, currency),
        ),
        reply_to=message_id,
    )
    return True


async def _maybe_handle_short_confirmation_reply(
    sender: str,
    message_id: str,
    text: str,
    company: Company,
    lang: str,
) -> bool:
    """Handle short agreement/rejection replies when they match a pending context."""
    reply_kind = classify_confirmation_text(text)
    if not reply_kind:
        return False

    phone_number = _extract_phone_number(sender)
    pending_remove_debt = await _get_pending_action(company.id, phone_number, PENDING_ACTION_REMOVE_DEBT_CONFIRM)
    pending_remove_change = await _get_pending_action(company.id, phone_number, PENDING_ACTION_REMOVE_CHANGE_CONFIRM)

    if pending_remove_debt:
        if reply_kind == "positive":
            await _remove_debtor_records_for_pending(company.id, pending_remove_debt.get("payload") or {})
        await _clear_pending_action(pending_remove_debt["id"])
        await _send_response(sender, t("ledger_query.done", lang=lang), reply_to=message_id)
        return True

    if pending_remove_change:
        if reply_kind == "positive":
            await _remove_change_records_for_pending(company.id, pending_remove_change.get("payload") or {})
        await _clear_pending_action(pending_remove_change["id"])
        await _send_response(sender, t("ledger_query.done", lang=lang), reply_to=message_id)
        return True

    # No pending actions matched this confirmation reply.
    # Return False so other handlers can process it normally.
    return False


async def _process_sales_query_message(
    sender: str,
    company: "Company | None",
    result,  # UnifiedMessageResult with sales_query data
    text: str,
    lang: str = DEFAULT_LANGUAGE,
) -> None:
    """
    Process a sales query request.

    Args:
        sender: The sender's phone number
        company: The company associated with the sender
        result: The unified parsing result with timeframe and optional product_filter
        lang: The user's preferred language
    """
    if not company:
        await _send_response(sender, t("sales_query.no_company", lang=lang))
        return

    try:
        normalized_timeframe = _normalize_sales_query_timeframe(text=text or "", llm_timeframe=result.timeframe)
        timeframe_label = _timeframe_label_for_response(normalized_timeframe, lang)

        # Get the date range based on timeframe
        start_date, end_date = _get_date_range(normalized_timeframe or "today")
        
        # Fetch sales data from the database
        sales_data = await _fetch_sales_for_query(
            company.id,
            start_date,
            end_date,
            result.product_filter,
        )

        if not sales_data["items"]:
            product_text = f" for {result.product_filter}" if result.product_filter else ""
            await _send_response(
                sender,
                t("sales_query.no_sales", lang=lang, timeframe=timeframe_label, product_text=product_text),
            )
            return

        # Build response message
        product_text = f" for {result.product_filter}" if result.product_filter else ""
        response_lines = [
            t("sales_query.header", lang=lang, timeframe=timeframe_label, product_text=product_text)
        ]

        # Add each item
        for item in sales_data["items"]:
            response_lines.append(
                t("sales_query.item", lang=lang,
                  quantity=item["quantity"],
                  product=item["product_name"],
                  price=item["unit_price"],
                  total=item["total"])
            )

        # Add grand total if all items have same currency
        if sales_data["grand_total"]:
            response_lines.append(
                t("sales_query.total_header", lang=lang, total=sales_data["grand_total"])
            )

        await _send_response(sender, "\n".join(response_lines))

    except Exception as e:
        logger.exception(f"Error processing sales query for company {company.id}: {e}")
        await _send_response(sender, t("sales_query.error", lang=lang))


async def _maybe_handle_ledger_query(
    sender: str,
    text: str,
    company: "Company | None",
    lang: str = DEFAULT_LANGUAGE,
) -> bool:
    """Handle debt/change question queries.

    Examples:
    - who has debt?
    - John have how much change?
    - how many debtors this month?
    """
    if not company:
        return False

    query_type = _detect_ledger_query_type(text)
    if not query_type:
        return False

    timeframe = _parse_ledger_timeframe(text)
    timeframe_label = _timeframe_label_for_response(timeframe, lang)
    normalized = _normalize_message_for_matching(text)
    wants_count = "how many" in normalized or "mangani" in normalized
    wants_list = any(token in normalized for token in ("who", "which", "show", "list", "ndiani"))

    subject_name = None
    if _has_explicit_person_reference(text) or (not wants_count and not wants_list):
        subject_name = _extract_ledger_subject_name(text)

    start_date, end_date = _get_date_range(timeframe)
    if timeframe == "all_time":
        start_date = None
        end_date = None

    data = await _fetch_ledger_query_data(
        company_id=company.id,
        query_type=query_type,
        start_date=start_date,
        end_date=end_date,
        subject_name=subject_name,
    )

    if subject_name:
        if not data["totals_by_person"]:
            key = "ledger_query.person_no_debt" if query_type == "debt" else "ledger_query.person_no_change"
            await _send_response(sender, t(key, lang=lang, name=subject_name, timeframe=timeframe_label))
            return True

        person = next(iter(sorted(data["totals_by_person"].keys())))
        totals = _format_currency_totals(data["totals_by_person"][person])
        key = "ledger_query.person_debt" if query_type == "debt" else "ledger_query.person_change"
        response_text = t(key, lang=lang, name=person, totals=totals, timeframe=timeframe_label)

        phone_number = _extract_phone_number(sender)
        if query_type == "debt":
            try:
                pending = await _set_pending_remove_debt_confirm(
                    company_id=company.id,
                    phone_number=phone_number,
                    debtor_name=person,
                    timeframe=timeframe,
                )
            except Exception:
                logger.exception("Failed creating remove-debt pending action for company=%s", company.id)
                pending = None

            if pending:
                prompt = t("ledger_query.settle_transaction_prompt", lang=lang)
                buttons = [
                    {
                        "id": f"remove_debt_yes_{pending['id']}",
                        "title": t("ledger_query.btn_paid_cancel", lang=lang),
                    },
                    {
                        "id": f"remove_debt_no_{pending['id']}",
                        "title": t("ledger_query.btn_keep_transaction", lang=lang),
                    },
                ]
                try:
                    message_sid = await _send_response_with_buttons(sender, f"{response_text}\n{prompt}", buttons)
                except Exception:
                    logger.exception("Failed sending debt-settlement buttons for company=%s", company.id)
                    message_sid = None

                if message_sid:
                    return True

                await _send_response(sender, response_text)
                return True

        if query_type == "change":
            try:
                pending = await _set_pending_remove_change_confirm(
                    company_id=company.id,
                    phone_number=phone_number,
                    customer_name=person,
                    timeframe=timeframe,
                )
            except Exception:
                logger.exception("Failed creating remove-change pending action for company=%s", company.id)
                pending = None

            if pending:
                prompt = t("ledger_query.settle_transaction_prompt", lang=lang)
                buttons = [
                    {
                        "id": f"remove_change_yes_{pending['id']}",
                        "title": t("ledger_query.btn_paid_cancel", lang=lang),
                    },
                    {
                        "id": f"remove_change_no_{pending['id']}",
                        "title": t("ledger_query.btn_keep_transaction", lang=lang),
                    },
                ]
                try:
                    message_sid = await _send_response_with_buttons(sender, f"{response_text}\n{prompt}", buttons)
                except Exception:
                    logger.exception("Failed sending change-settlement buttons for company=%s", company.id)
                    message_sid = None

                if message_sid:
                    return True

                await _send_response(sender, response_text)
                return True

        await _send_response(sender, response_text)
        return True

    if wants_count:
        key = "ledger_query.debtor_count" if query_type == "debt" else "ledger_query.change_count"
        await _send_response(sender, t(key, lang=lang, timeframe=timeframe_label, count=data["people_count"]))
        return True

    if not data["totals_by_person"]:
        key = "ledger_query.no_debt_records" if query_type == "debt" else "ledger_query.no_change_records"
        await _send_response(sender, t(key, lang=lang, timeframe=timeframe_label))
        return True

    header_key = "ledger_query.debt_header" if query_type == "debt" else "ledger_query.change_header"
    lines = [t(header_key, lang=lang, timeframe=timeframe_label)]
    for name, totals_by_currency in sorted(data["totals_by_person"].items())[:20]:
        lines.append(
            t(
                "ledger_query.line",
                lang=lang,
                name=name,
                totals=_format_currency_totals(totals_by_currency),
            )
        )
    await _send_response(sender, "\n".join(lines))
    return True


def _get_date_range(timeframe: str) -> tuple:
    """Calculate start and end dates based on timeframe string.

    Returns:
        A tuple of (start_date, end_date) as aware datetime objects
    """
    now = timezone.now()
    
    if timeframe == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe == "yesterday":
        yesterday = now - timedelta(days=1)
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe == "week":
        # Last 7 days
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe == "last_week":
        start_of_this_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        start = start_of_this_week - timedelta(days=7)
        end = (start_of_this_week - timedelta(microseconds=1))
    elif timeframe == "month":
        # Last 30 days
        start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe == "last_month":
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = first_of_this_month - timedelta(microseconds=1)
        start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif timeframe == "year":
        # Last 365 days
        start = (now - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe == "last_year":
        start = now.replace(year=now.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(year=now.year - 1, month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe.startswith("date_"):
        try:
            requested_date = datetime.strptime(timeframe.split("_", 1)[1], "%Y-%m-%d")
            start = timezone.make_aware(requested_date.replace(hour=0, minute=0, second=0, microsecond=0))
            end = timezone.make_aware(requested_date.replace(hour=23, minute=59, second=59, microsecond=999999))
        except Exception:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe and timeframe.endswith("_days"):
        # Custom number of days (e.g., "2_days")
        try:
            num_days = int(timeframe.split("_")[0])
            start = (now - timedelta(days=num_days)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        except (ValueError, IndexError):
            # Default to today if parsing fails
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif timeframe == "all_time":
        start = now - timedelta(days=3650)
        end = now
    else:
        # Default to today
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    return start, end


@db_sync_to_async
def _fetch_sales_for_query(
    company_id: int,
    start_date,
    end_date,
    product_filter: str | None = None,
) -> dict:
    """Fetch sales data for a given company and date range.

    Returns:
        A dictionary with 'items' (list of sale items) and 'grand_total' (formatted total)
    """
    from apps.sales.models import SaleItem
    
    # Query sales within date range
    sales_items = SaleItem.objects.filter(
        sale__company_id=company_id,
        sale__sale_timestamp__gte=start_date,
        sale__sale_timestamp__lte=end_date,
        sale__status=Sale.Status.CONFIRMED,
    ).select_related("product", "sale")

    # Filter by product if specified
    if product_filter:
        sales_items = sales_items.filter(product__name__icontains=product_filter)

    # Aggregate by product
    items_dict = {}
    for item in sales_items:
        key = (item.product.name, item.currency)
        if key not in items_dict:
            items_dict[key] = {
                "quantity": 0,
                "product_name": item.product.name,
                "unit_price": format_price(item.unit_price, item.currency) if item.unit_price else "N/A",
                "total": Decimal("0"),
                "currency": item.currency,
            }
        items_dict[key]["quantity"] += item.quantity
        if item.unit_price:
            items_dict[key]["total"] += Decimal(str(item.quantity)) * item.unit_price

    # Format results
    result_items = []
    currencies_used = set()
    grand_total_by_currency = {}

    for (product_name, currency), data in items_dict.items():
        currencies_used.add(currency)
        if currency not in grand_total_by_currency:
            grand_total_by_currency[currency] = Decimal("0")
        grand_total_by_currency[currency] += data["total"]
        
        result_items.append({
            "quantity": data["quantity"],
            "product_name": product_name,
            "unit_price": data["unit_price"],
            "total": format_price(data["total"], currency),
        })

    # Format grand total
    grand_total = None
    # Filter out None currencies
    valid_currencies = {k: v for k, v in grand_total_by_currency.items() if k is not None}
    
    if len(valid_currencies) == 1:
        # All items in same currency
        currency = list(valid_currencies.keys())[0]
        total_amount = valid_currencies[currency]
        grand_total = format_price(total_amount, currency)
    elif valid_currencies:
        # Multiple currencies - show breakdown
        parts = []
        for currency in sorted(valid_currencies.keys()):
            parts.append(format_price(valid_currencies[currency], currency))
        grand_total = " + ".join(parts)
    elif grand_total_by_currency.get(None):
        # Items without currency
        total_amount = grand_total_by_currency[None]
        grand_total = f"{total_amount} (no currency)"

    return {
        "items": result_items,
        "grand_total": grand_total,
    }


@db_sync_to_async
def _fetch_ledger_query_data(
    company_id: int,
    query_type: str,
    start_date,
    end_date,
    subject_name: str | None = None,
) -> dict:
    if query_type == "debt":
        records = DebtorRecord.objects.filter(company_id=company_id)
        person_field = "debtor_name"
    else:
        records = ChangeRecord.objects.filter(company_id=company_id)
        person_field = "customer_name"

    if start_date is not None and end_date is not None:
        records = records.filter(recorded_at__gte=start_date, recorded_at__lte=end_date)

    if subject_name:
        records = records.filter(**{f"{person_field}__icontains": subject_name})

    totals_by_person: dict[str, dict[str, Decimal]] = {}
    for row in records:
        person = getattr(row, person_field) or "Unknown"
        currency = row.currency or "USD"
        if person not in totals_by_person:
            totals_by_person[person] = {}
        totals_by_person[person][currency] = totals_by_person[person].get(currency, Decimal("0.00")) + Decimal(str(row.amount))

    return {
        "totals_by_person": totals_by_person,
        "people_count": len(totals_by_person),
    }


def handle_sale_button_action(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Handle a sale button action (confirm/fix) from WhatsApp button click.

    Args:
        action: "confirm" or "fix"
        sender: The sender's phone number (e.g., whatsapp:+1234567890)
        original_message_sid: The SID of the message being replied to
    """
    try:
        close_old_connections()
        run_async(_process_sale_button_action_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling sale {action}: {e}")


def handle_record_change_button_action(
    sender: str,
    sale_id: int,
    original_message_sid: str | None = None,
) -> None:
    """Handle record change button clicks by prompting for customer name."""
    try:
        close_old_connections()
        run_async(_process_record_change_button_action_async(sender, sale_id, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling record_change for sale={sale_id}: {e}")


def handle_add_debtor_button_action(
    sender: str,
    sale_id: int,
    amount_cents: int,
    currency: str,
    original_message_sid: str | None = None,
) -> None:
    """Handle add-debtor button clicks by prompting for debtor name."""
    try:
        close_old_connections()
        run_async(
            _process_add_debtor_button_action_async(
                sender,
                sale_id,
                amount_cents,
                currency,
                original_message_sid,
            )
        )
    except Exception as e:
        logger.exception(f"Error handling add_debtor for sale={sale_id}: {e}")


def handle_debtor_record_decision_button_action(
    action: str,
    sender: str,
    pending_action_id: int,
    original_message_sid: str | None = None,
) -> None:
    """Handle record/dont-record decision after invalid debtor-name input."""
    try:
        close_old_connections()
        run_async(
            _process_debtor_record_decision_button_action_async(
                action,
                sender,
                pending_action_id,
                original_message_sid,
            )
        )
    except Exception as e:
        logger.exception(f"Error handling debtor decision action={action}: {e}")


def handle_remove_debt_button_action(
    action: str,
    sender: str,
    pending_action_id: int,
    original_message_sid: str | None = None,
) -> None:
    """Handle remove-debt yes/no decisions from ledger query responses."""
    try:
        close_old_connections()
        run_async(
            _process_remove_debt_button_action_async(
                action,
                sender,
                pending_action_id,
                original_message_sid,
            )
        )
    except Exception as e:
        logger.exception(f"Error handling remove_debt action={action}: {e}")


async def _process_remove_debt_button_action_async(
    action: str,
    sender: str,
    pending_action_id: int,
    original_message_sid: str | None = None,
) -> None:
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    if not profile or not profile.company_id:
        await _send_response(sender, t("error.processing_failed", lang=lang))
        return

    pending = await _get_pending_action_by_id(profile.company_id, phone_number, pending_action_id)
    if not pending or pending.get("action_type") != PENDING_ACTION_REMOVE_DEBT_CONFIRM:
        await _send_response(sender, t("sale.already_processed", lang=lang), reply_to=original_message_sid)
        return

    if action == "no":
        await _clear_pending_action(pending_action_id)
        await _send_response(sender, t("ledger_query.done", lang=lang), reply_to=original_message_sid)
        return

    await _remove_debtor_records_for_pending(profile.company_id, pending.get("payload") or {})
    await _clear_pending_action(pending_action_id)
    await _send_response(
        sender,
        t("ledger_query.done", lang=lang),
        reply_to=original_message_sid,
    )


def handle_remove_change_button_action(
    action: str,
    sender: str,
    pending_action_id: int,
    original_message_sid: str | None = None,
) -> None:
    """Handle remove-change yes/no decisions from ledger query responses."""
    try:
        close_old_connections()
        run_async(
            _process_remove_change_button_action_async(
                action,
                sender,
                pending_action_id,
                original_message_sid,
            )
        )
    except Exception as e:
        logger.exception(f"Error handling remove_change action={action}: {e}")


async def _process_remove_change_button_action_async(
    action: str,
    sender: str,
    pending_action_id: int,
    original_message_sid: str | None = None,
) -> None:
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    if not profile or not profile.company_id:
        await _send_response(sender, t("error.processing_failed", lang=lang))
        return

    pending = await _get_pending_action_by_id(profile.company_id, phone_number, pending_action_id)
    if not pending or pending.get("action_type") != PENDING_ACTION_REMOVE_CHANGE_CONFIRM:
        await _send_response(sender, t("sale.already_processed", lang=lang), reply_to=original_message_sid)
        return

    if action == "yes":
        await _remove_change_records_for_pending(profile.company_id, pending.get("payload") or {})

    await _clear_pending_action(pending_action_id)
    await _send_response(sender, t("ledger_query.done", lang=lang), reply_to=original_message_sid)


async def _process_record_change_button_action_async(
    sender: str,
    sale_id: int,
    original_message_sid: str | None = None,
) -> None:
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    if not profile or not profile.company_id:
        await _send_response(sender, t("error.processing_failed", lang=lang))
        return

    company_id = profile.company_id

    existing_pending = await _get_pending_action(
        company_id,
        phone_number,
        PENDING_ACTION_RECORD_CHANGE_NAME,
    )
    if existing_pending:
        await _send_response(sender, t("payment.change_name_prompt", lang=lang), reply_to=original_message_sid)
        return

    change_info = await _get_sale_change_info(company_id, sale_id)
    if not change_info:
        await _send_response(sender, t("sale.already_processed", lang=lang), reply_to=original_message_sid)
        return

    await _set_pending_record_change_name(
        company_id=company_id,
        phone_number=phone_number,
        sale_id=sale_id,
        amount=change_info["amount"],
        currency=change_info["currency"],
    )
    await _send_response(sender, t("payment.change_name_prompt", lang=lang), reply_to=original_message_sid)


@db_sync_to_async
def _get_pending_action_by_id(company_id: int, phone_number: str, pending_action_id: int) -> dict | None:
    pending = PendingAction.objects.filter(
        id=pending_action_id,
        company_id=company_id,
        phone_number=phone_number,
    ).first()
    if not pending:
        return None
    return {
        "id": pending.id,
        "action_type": pending.action_type,
        "payload": pending.payload or {},
    }


async def _process_add_debtor_button_action_async(
    sender: str,
    sale_id: int,
    amount_cents: int,
    currency: str,
    original_message_sid: str | None = None,
) -> None:
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    if not profile or not profile.company_id:
        await _send_response(sender, t("error.processing_failed", lang=lang))
        return

    amount = (Decimal(amount_cents) / Decimal("100")).quantize(Decimal("0.01"))
    pending = await _set_pending_record_debtor_name(
        company_id=profile.company_id,
        phone_number=phone_number,
        sale_id=sale_id,
        amount=amount,
        currency=currency,
    )
    if not pending:
        await _send_response(sender, t("error.processing_failed", lang=lang), reply_to=original_message_sid)
        return

    await _send_response(sender, t("payment.debtor_name_prompt", lang=lang), reply_to=original_message_sid)


async def _process_debtor_record_decision_button_action_async(
    action: str,
    sender: str,
    pending_action_id: int,
    original_message_sid: str | None = None,
) -> None:
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    if not profile or not profile.company_id:
        await _send_response(sender, t("error.processing_failed", lang=lang))
        return

    pending = await _get_pending_action_by_id(profile.company_id, phone_number, pending_action_id)
    if not pending or pending.get("action_type") != PENDING_ACTION_RECORD_DEBTOR_NAME:
        await _send_response(sender, t("sale.already_processed", lang=lang), reply_to=original_message_sid)
        return

    if action == "record":
        await _send_response(sender, t("payment.debtor_name_prompt", lang=lang), reply_to=original_message_sid)
        return

    await _clear_pending_action(pending_action_id)
    await _send_response(sender, t("payment.not_recorded", lang=lang), reply_to=original_message_sid)


@db_sync_to_async
def _get_confirmed_sale(original_message_sid: str | None) -> tuple[Sale, str | None] | None:
    """Find a confirmed sale by response message SID (read-only, no status change).

    Returns:
        A tuple of (sale, whatsapp_message_id) if found, None otherwise.
    """
    if not original_message_sid:
        logger.warning("No original message SID provided")
        return None

    try:
        sale = Sale.objects.get(
            confirmation_message_sid=original_message_sid,
            status=Sale.Status.CONFIRMED,
        )
        return sale, sale.whatsapp_message_id
    except Sale.DoesNotExist:
        logger.warning(f"No confirmed sale found for message SID: {original_message_sid}")
        return None


@db_sync_to_async
def _get_and_update_sale(original_message_sid: str | None, new_status: str, bot_mistake: bool = False) -> tuple[Sale, str | None] | None:
    """Find the sale by response message SID and update its status.

    Returns:
        A tuple of (sale, whatsapp_message_id) if found, None otherwise.
    """
    if not original_message_sid:
        logger.warning("No original message SID provided")
        return None

    try:
        sale = Sale.objects.get(
            confirmation_message_sid=original_message_sid,
            status=Sale.Status.CONFIRMED,
        )
        sale.status = new_status
        if bot_mistake:
            sale.flagged_as_bot_mistake = True
        sale.save(update_fields=["status", "flagged_as_bot_mistake"])
        return sale, sale.whatsapp_message_id
    except Sale.DoesNotExist:
        logger.warning(f"No confirmed sale found for message SID: {original_message_sid}")
        return None


async def _process_sale_button_action_async(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Async processing of sale button action.

    Args:
        action: "confirm" or "fix"
        sender: The sender's phone number
        original_message_sid: The SID of the message being replied to
    """
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    if action == "confirm":
        # Sale is already confirmed, just acknowledge
        result = await _get_confirmed_sale(original_message_sid)
        if result:
            sale, original_whatsapp_message_id = result
            await _send_response(
                sender, t("sale.confirmed_ok", lang=lang), reply_to=original_whatsapp_message_id
            )
        else:
            await _send_response(sender, t("sale.already_processed", lang=lang))
    else:
        # "fix" — cancel the sale and flag as bot mistake
        result = await _get_and_update_sale(original_message_sid, Sale.Status.CANCELLED, bot_mistake=True)
        if result:
            sale, original_whatsapp_message_id = result
            await _send_response(
                sender, t("sale.fix_guided", lang=lang), reply_to=original_whatsapp_message_id
            )
        else:
            await _send_response(sender, t("sale.already_processed", lang=lang))


def handle_waitlist_button_action(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Handle a waitlist approval/rejection from WhatsApp button click.

    Args:
        action: "approve" or "reject"
        sender: The sender's phone number (e.g., whatsapp:+1234567890)
        original_message_sid: The SID of the message being replied to
    """
    try:
        close_old_connections()
        run_async(_process_waitlist_button_action_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling waitlist {action}: {e}")


@db_sync_to_async
def _get_and_update_waitlist_entry(original_message_sid: str | None, action: str) -> WaitlistEntry | None:
    """Find the waitlist entry by response message SID and update its status."""
    if not original_message_sid:
        logger.warning("No original message SID provided for waitlist button action")
        return None

    try:
        entry = WaitlistEntry.objects.get(
            confirmation_message_sid=original_message_sid,
            status=WaitlistEntry.Status.PENDING,
        )
        if action == "approve":
            entry.status = WaitlistEntry.Status.APPROVED
        else:
            entry.status = WaitlistEntry.Status.REJECTED
        entry.save(update_fields=["status"])
        return entry
    except WaitlistEntry.DoesNotExist:
        logger.warning(f"No pending waitlist entry found for message SID: {original_message_sid}")
        return None


@db_sync_to_async
def _approve_waitlist_entry(entry: WaitlistEntry) -> tuple[Company, UserProfile]:
    """Run the full approval logic for a waitlist entry (async wrapper)."""
    from apps.core.services import approve_waitlist_entry
    return approve_waitlist_entry(entry)


async def _process_waitlist_button_action_async(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Async processing of waitlist button action.

    Args:
        action: "approve" or "reject"
        sender: The sender's phone number
        original_message_sid: The SID of the message being replied to
    """
    entry = await _get_and_update_waitlist_entry(original_message_sid, action)

    if not entry:
        await _send_response(sender, t("waitlist.already_processed"))
        return

    lang = entry.language

    if action == "approve":
        # Run the full approval logic
        company, profile = await _approve_waitlist_entry(entry)

        # Notify admin
        await _send_response(
            sender,
            t("waitlist_admin.approved", phone=entry.phone_number, company=company.name),
        )

        # Send approval notification to the user in their language
        await _send_response(
            entry.phone_number,
            t("approval.welcome", lang=lang, company=company.name),
        )
    else:
        # Notify admin
        await _send_response(
            sender,
            t("waitlist_admin.rejected", phone=entry.phone_number),
        )

        # Notify the user in their language
        await _send_response(entry.phone_number, t("waitlist.rejected", lang=lang))
