"""Service for parsing sale messages using OpenRouter LLM."""

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any

from asgiref.sync import sync_to_async
from django.db import close_old_connections

from apps.catalog.models import Product
from apps.sales.services import ParsedSaleItem
from services.openrouter import OpenRouterClient
from utils.timing import track

logger = logging.getLogger(__name__)

_PRODUCT_CACHE_TTL_SECONDS = 60
_PRODUCT_CACHE: dict[int | None, tuple[float, list[Product]]] = {}


def _currency_token_to_code(token: str) -> str | None:
    t = re.sub(r"\s+", " ", token.lower().strip())
    if t in ("$", "usd", "dollar", "dollars", "us dollar", "us dollars", "dolar", "dolars", "doller", "dollers"):
        return "USD"
    if t in ("zig", "zwg"):
        return "ZWG"
    if t in ("r", "rand", "rands", "zar"):
        return "ZAR"
    if t in ("p", "pula", "bwp"):
        return "BWP"
    if t in ("€", "eur", "euro"):
        return "EUR"
    if t in ("£", "gbp", "pound", "pounds"):
        return "GBP"
    return None


def _apply_rule_based_price_hints(
    message: str,
    parsed_items: list[ParsedSaleItem],
    detected_currency: str | None,
) -> tuple[list[ParsedSaleItem], str | None]:
    """Apply lightweight rule-based hints to parsed items.

    Current rule: when an item has a numeric price and quantity > 1, do NOT assume
    the provided price is a per-unit price unless the message contains an explicit
    per-unit indicator (for example: "each", "per", "apiece", "one", "individual",
    "one by one", "respectively", "any one", "particular", "separate", "every").

    If no per-unit marker is present, clear the unit price so downstream code does
    not treat "3 eggs $2" as $2 each.
    """
    if not message or not parsed_items:
        return parsed_items, detected_currency

    # Tokens/phrases that indicate a per-unit price
    per_unit_tokens = (
        r"\beach\b",
        r"\bone\b",
        r"\bevery\b",
        r"\bindividual\b",
        r"\bevery\s+single\b",
        r"\bone\s+by\s+one\b",
        r"\bper\b",
        r"\bapiece\b",
        r"\brespectively\b",
        r"\bany\s+one\b",
        r"\bparticular\b",
        r"\bseparate\b",
        r"\bimwe\b",  # Shona: one/each (per-unit)
    )

    per_unit_pattern = re.compile(r"(?:" + "|".join(per_unit_tokens) + r")", re.IGNORECASE)

    # Also consider shorthand '@' as a per-unit marker ("3 eggs @ $2")
    has_per_unit_marker = bool(per_unit_pattern.search(message)) or "@" in (message or "")

    adjusted: list[ParsedSaleItem] = []
    for item in parsed_items:
        adjusted_item = dict(item)
        try:
            qty = int(adjusted_item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1

        unit_price = adjusted_item.get("unit_price")
        if unit_price is not None and qty > 1 and not has_per_unit_marker:
            adjusted_item["declared_total_amount"] = unit_price
            adjusted_item["unit_price"] = None
        elif "declared_total_amount" not in adjusted_item:
            adjusted_item["declared_total_amount"] = None

        adjusted.append(adjusted_item)

    return adjusted, detected_currency


@dataclass
class ParsedSaleMessage:
    """Result of parsing a sale message."""
    items: list[ParsedSaleItem]
    currency: str | None = None


@dataclass
class UnifiedMessageResult:
    """Result of unified message parsing (intent + data extraction)."""
    intent: str  # "sale" | "add_assistant" | "sales_query" | "other"
    confidence: float

    # Sale-specific (populated if intent="sale")
    items: list[ParsedSaleItem] = field(default_factory=list)
    currency: str | None = None

    # Add assistant-specific (populated if intent="add_assistant")
    phone_number: str | None = None

    # Sales query-specific (populated if intent="sales_query")
    timeframe: str | None = None  # "today", "yesterday", "week", "month", "year", "X_days", etc.
    product_filter: str | None = None  # Optional product name for filtering

    # Optional metadata
    notes: str | None = None


@sync_to_async
def _get_active_products(company=None):
    """Fetch active products scoped to a company (sync wrapper for async context)."""
    company_id = company.id if company else None
    cached_entry = _PRODUCT_CACHE.get(company_id)
    now = monotonic()
    if cached_entry and (now - cached_entry[0]) < _PRODUCT_CACHE_TTL_SECONDS:
        return cached_entry[1]

    close_old_connections()
    queryset = Product.objects.filter(active=True)
    if company:
        queryset = queryset.filter(company=company)
    products = list(queryset)
    _PRODUCT_CACHE[company_id] = (now, products)
    return products


async def parse_message_unified(message: str, company=None) -> UnifiedMessageResult:
    """
    Parse message using unified LLM call (intent detection + data extraction).

    Replaces two-step: detect_message_intent() + parse_sale_message()

    Args:
        message: The message text to parse
        company: The company to scope product lookup to (prevents cross-shop leaks)
    """
    async with track("unified_parse"):
        # Fetch products scoped to company so we don't leak across shops
        products = await _get_active_products(company)
        from services.openrouter import build_unified_parsing_prompt
        messages = build_unified_parsing_prompt(message, products)

        client = OpenRouterClient()

        try:
            response = await client.parse_json_response(messages)
        except Exception as e:
            logger.exception(f"Failed to parse message: {e}")
            raise

        logger.info(f"LLM response for '{message[:80]}': {response}")

        # Extract intent and confidence
        intent = response.get("intent", "sale")
        confidence = response.get("confidence", 0.5)

        result = UnifiedMessageResult(
            intent=intent,
            confidence=confidence,
            notes=response.get("notes"),
        )

        # Extract intent-specific data
        if intent == "sale":
            # Parse sale items
            items = response.get("items", [])
            parsed_items: list[ParsedSaleItem] = []

            for item in items:
                try:
                    unit_price = None
                    if item.get("unit_price") is not None:
                        try:
                            unit_price = Decimal(str(item["unit_price"]))
                        except (InvalidOperation, ValueError):
                            pass

                    parsed_items.append(
                        ParsedSaleItem(
                            product_name=str(item.get("product_name", "")),
                            quantity=int(item.get("quantity", 1)),
                            unit_price=unit_price,
                            declared_total_amount=None,
                        )
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(f"Skipping invalid item {item}: {e}")
                    continue

            result.items = parsed_items

            # Extract and validate currency
            currency = response.get("currency")
            if currency and currency in ("USD", "ZWG", "ZAR", "BWP", "EUR", "GBP"):
                result.currency = currency
            elif currency:
                logger.warning(f"Unknown currency {currency}, ignoring")

            # Fallback: parse natural phrasing where LLM may miss price/currency.
            result.items, inferred_currency = _apply_rule_based_price_hints(
                message,
                result.items,
                result.currency,
            )
            if not result.currency and inferred_currency:
                result.currency = inferred_currency

        elif intent == "add_assistant":
            result.phone_number = response.get("phone_number")

        elif intent == "sales_query":
            result.timeframe = response.get("timeframe", "today")
            result.product_filter = response.get("product_filter")

        return result
