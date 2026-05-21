"""
Intent and slot extraction parser for tuck shop sales/business messages.

Handles:
- Intent classification (sales.record, report.daily_summary, etc.)
- Slot extraction (quantity, product_name, total_amount, unit_price, currency, etc.)
- Multilingual support (English, Shona, Zulu, Ndebele, Afrikaans)
- Typo tolerance (Levenshtein distance)
- Normalization (numbers, currency, time references)
"""

import re
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple, Any
from pathlib import Path
import yaml
from difflib import SequenceMatcher
from datetime import datetime, timedelta


@dataclass
class ParsedSlots:
    """Extracted slot values from user message."""
    quantity: Optional[int] = None
    product_name: Optional[str] = None
    total_amount: Optional[float] = None
    unit_price: Optional[float] = None
    currency: Optional[str] = None
    payment_method: Optional[str] = None
    time_reference: Optional[str] = None
    raw_items: List[Dict[str, Any]] = None  # For multi-item sales

    def __post_init__(self):
        if self.raw_items is None:
            self.raw_items = []

    def to_dict(self) -> Dict:
        return {
            "quantity": self.quantity,
            "product_name": self.product_name,
            "total_amount": self.total_amount,
            "unit_price": self.unit_price,
            "currency": self.currency,
            "payment_method": self.payment_method,
            "time_reference": self.time_reference,
            "raw_items": self.raw_items,
        }


@dataclass
class ParseResult:
    """Result of intent + slot parsing."""
    intent_id: str
    intent_name: str
    confidence: float
    slots: ParsedSlots
    raw_message: str
    normalized_message: str
    clarifications_needed: List[str] = None

    def __post_init__(self):
        if self.clarifications_needed is None:
            self.clarifications_needed = []

    def to_dict(self) -> Dict:
        return {
            "intent_id": self.intent_id,
            "intent_name": self.intent_name,
            "confidence": self.confidence,
            "slots": self.slots.to_dict(),
            "raw_message": self.raw_message,
            "normalized_message": self.normalized_message,
            "clarifications_needed": self.clarifications_needed,
        }


class IntentParser:
    """Parser for tuck shop sales and business intents."""

    def __init__(self, training_data_path: Optional[str] = None):
        """
        Initialize parser with training data.

        Args:
            training_data_path: Path to training_data.yaml. If None, loads from default location.
        """
        if training_data_path is None:
            training_data_path = (
                Path(__file__).parent / "training_data.yaml"
            )
        
        with open(training_data_path, "r") as f:
            self.training_data = yaml.safe_load(f)

        self._build_intent_index()
        self._build_product_synonyms()
        self._build_currency_map()
        self._build_payment_keywords()

    def _build_intent_index(self):
        """Build index for fast intent lookup."""
        self.intents_by_id = {}
        self.intent_synonyms = {}

        for intent in self.training_data["intents"]:
            intent_id = intent["intent_id"]
            self.intents_by_id[intent_id] = intent
            for synonym in intent.get("synonyms", []):
                self.intent_synonyms[synonym.lower()] = intent_id

    def _build_product_synonyms(self):
        """Build product normalization map."""
        self.product_synonyms = {}
        product_slot = self.training_data["slots"]["product_name"]
        for canonical, synonyms in product_slot.get("normalized_synonyms", {}).items():
            for syn in synonyms:
                self.product_synonyms[syn.lower()] = canonical.lower()

    def _build_currency_map(self):
        """Build currency normalization map."""
        self.currency_map = {}
        currency_map_data = self.training_data["normalization"].get("currency_symbols", {})
        for raw_symbol, normalized_code in currency_map_data.items():
            self.currency_map[str(raw_symbol).lower()] = normalized_code

    def _build_payment_keywords(self):
        """Build payment method lookup."""
        self.payment_keywords = {}
        payment_slot = self.training_data["slots"]["payment_method"]
        for method, keywords in payment_slot.get("keywords", {}).items():
            for kw in keywords:
                self.payment_keywords[kw.lower()] = method

    def parse(self, message: str) -> ParseResult:
        """
        Parse a user message and extract intent + slots.

        Args:
            message: Raw user message (text or transcription)

        Returns:
            ParseResult with intent, confidence, and extracted slots
        """
        # Normalize the message first
        normalized = self._normalize_message(message)
        lower_msg = normalized.lower()

        # Classify intent
        intent_id, intent_name, confidence = self._classify_intent(normalized)
        
        # Extract slots based on intent
        slots = self._extract_slots(normalized, intent_id)
        
        # Determine clarifications needed
        clarifications = self._determine_clarifications(intent_id, slots)

        return ParseResult(
            intent_id=intent_id,
            intent_name=intent_name,
            confidence=confidence,
            slots=slots,
            raw_message=message,
            normalized_message=normalized,
            clarifications_needed=clarifications,
        )

    def _normalize_message(self, message: str) -> str:
        """
        Normalize message: fix typos, expand contractions, standardize formatting.
        """
        # Expand contractions
        for contraction, expansion in self.training_data["normalization"]["contractions"].items():
            message = re.sub(rf"\b{re.escape(contraction)}\b", expansion, message, flags=re.IGNORECASE)

        # Convert numbers (three -> 3, etc.)
        number_words = self.training_data["normalization"]["number_words"]
        for word, digit in number_words.items():
            message = re.sub(rf"\b{word}\b", str(digit), message, flags=re.IGNORECASE)

        # Remove common filler words from voice/text transcriptions
        fillers = [r"\bum\b", r"\buh\b", r"\blike\b", r"\bjust\b", r"\bactually\b", r"\bplease\b"]
        for f in fillers:
            message = re.sub(f, "", message, flags=re.IGNORECASE)

        # Normalize spacing and punctuation
        message = re.sub(r"[\s]{2,}", " ", message)
        message = message.strip()

        return message

    def _classify_intent(self, message: str) -> Tuple[str, str, float]:
        """
        Classify message intent based on training utterances.

        Returns:
            (intent_id, intent_name, confidence_score)
        """
        scores = {}

        lower = message.lower()
        for intent in self.training_data["intents"]:
            intent_id = intent["intent_id"]
            utterances = intent.get("training_utterances", [])
            
            # Similarity score: match against training utterances
            max_similarity = max(
                (self._string_similarity(message.lower(), utt.lower()) for utt in utterances),
                default=0.0
            )
            # boost score based on presence of intent synonyms/keyword hits
            hits = 0
            for kw in intent.get("synonyms", []):
                if kw.lower() in lower:
                    hits += 1
            scores[intent_id] = max_similarity + (0.2 * hits)

        # Find best match
        best_intent_id = max(scores, key=scores.get)
        best_confidence = scores[best_intent_id]

        intent = self.intents_by_id[best_intent_id]
        intent_name = intent["canonical_name"]

        # Inventory cue override: explicit inventory/restock messages should prefer inventory.update
        if re.search(r'\b(stock|restock|add stock|new stock|refill|inventory|restock|delivery|restocking|restock)\b', lower):
            return 'inventory.update', self.intents_by_id['inventory.update']['canonical_name'], max(best_confidence, 0.7)

        # Business/status specific overrides
        if re.search(r'\b(most sold|which product sold|top items|most sold products)\b', lower):
            return 'business.status', self.intents_by_id['business.status']['canonical_name'], max(best_confidence, 0.7)

        # Finance/profit specific overrides
        if re.search(r'\b(how much (?:did we make|came in)|profit|total sales amount|cash made)\b', lower):
            return 'finance.profit_query', self.intents_by_id['finance.profit_query']['canonical_name'], max(best_confidence, 0.7)

        # Heuristic override: if message looks like a sale (has currency symbols/words and a product/quantity), prefer sales.record
        has_price = bool(re.search(r'(\$|\bR\b|\brand\b|\bzar\b|\busd\b)', lower, re.IGNORECASE)) or bool(re.search(r'\d+\s*(?:rand|r|zar|usd)\b', lower, re.IGNORECASE))
        has_qty = bool(re.search(r'\b\d+\b', lower))
        has_product = self._extract_product_name(message) is not None
        if (has_price or has_qty) and has_product:
            return 'sales.record', self.intents_by_id['sales.record']['canonical_name'], max(best_confidence, 0.6)

        return best_intent_id, intent_name, best_confidence

    def _extract_slots(self, message: str, intent_id: str) -> ParsedSlots:
        """Extract slots based on intent and message."""
        slots = ParsedSlots()

        if intent_id == "sales.record":
            slots = self._extract_sales_slots(message)
        elif intent_id == "report.daily_summary":
            slots.time_reference = self._extract_time_reference(message)
        elif intent_id == "shop.closing":
            slots.time_reference = self._extract_closing_time(message)
        elif intent_id == "inventory.update":
            slots.product_name = self._extract_product_name(message)
            slots.quantity = self._extract_quantity(message)
        elif intent_id == "finance.profit_query":
            slots.time_reference = self._extract_time_reference(message)
        elif intent_id == "business.status":
            slots.time_reference = self._extract_time_reference(message)

        return slots

    def _extract_sales_slots(self, message: str) -> ParsedSlots:
        """Extract slots for sales.record intent: quantity, product, price, currency, payment method."""
        slots = ParsedSlots()
        
        # Extract multiple items (e.g., "2 cokes and 3 chips")
        items = self._extract_items(message)
        
        if items:
            slots.raw_items = items
            # Compute total_amount where possible from unit_price * quantity
            for it in slots.raw_items:
                if it.get("unit_price") is not None and it.get("quantity") is not None and not it.get("total_amount"):
                    try:
                        it["total_amount"] = float(it["unit_price"]) * int(it["quantity"])
                    except Exception:
                        pass
            # If single item, populate top-level slots (and ensure total computed)
            if len(items) == 1:
                item = slots.raw_items[0]
                slots.quantity = item.get("quantity")
                slots.product_name = item.get("product_name")
                slots.unit_price = item.get("unit_price")
                slots.currency = item.get("currency")
                if item.get("total_amount") is not None:
                    slots.total_amount = item.get("total_amount")
                elif item.get("unit_price") is not None and item.get("quantity") is not None:
                    try:
                        slots.total_amount = float(item.get("unit_price")) * int(item.get("quantity"))
                    except Exception:
                        slots.total_amount = None
        else:
            # Fallback: extract single values
            slots.quantity = self._extract_quantity(message)
            slots.product_name = self._extract_product_name(message)
            (slots.total_amount, slots.unit_price, slots.currency) = self._extract_prices(message)
            # compute total if we have unit_price + quantity but no total_amount
            if slots.unit_price is not None and slots.quantity is not None and not slots.total_amount:
                try:
                    slots.total_amount = float(slots.unit_price) * int(slots.quantity)
                except Exception:
                    pass

        slots.payment_method = self._extract_payment_method(message)
        return slots

    def _extract_items(self, message: str) -> List[Dict[str, Any]]:
        """
        Extract multiple items from message.
        E.g., "2 cokes R20 each and 3 chips R100"
        """
        items = []
        msg = message.strip()
        lower = msg.lower()

        # 1) Price-first with explicit 'for' => total amount for quantity
        # e.g. "$5 for 2 bread" => total_amount=5, quantity=2
        m = re.search(r'(?P<cur>\$|r|rand|z?ar|usd)\s*(?P<amount>\d+(?:\.\d{1,2})?)\s+for\s+(?P<qty>\d+)\s+(?P<prod>[\w]+)', lower, re.IGNORECASE)
        if m:
            amt = float(m.group('amount'))
            qty = int(m.group('qty'))
            prod = self._normalize_product_name(m.group('prod'))
            currency_raw = m.group('cur')
            currency = self.currency_map.get(str(currency_raw).lower())
            items.append({
                'quantity': qty,
                'product_name': prod,
                'total_amount': amt,
                'unit_price': None,
                'currency': currency,
            })
            return items

        # 2) Price-first without 'for': treat conservatively as TOTAL unless per-unit cue nearby
        # e.g. "$2 3 mazai" -> amount=2, qty=3, prod=mazai (total by default)
        m = re.search(r'(?P<cur>\$|r|rand|z?ar|usd)\s*(?P<amount>\d+(?:\.\d{1,2})?)\s+(?P<qty>\d+)\s+(?P<prod>[\w]+)', lower, re.IGNORECASE)
        if m:
            amt = float(m.group('amount'))
            qty = int(m.group('qty'))
            prod = self._normalize_product_name(m.group('prod'))
            ctx = lower
            has_per_unit_cue = any(cue in ctx for cue in self.training_data['slots']['unit_price']['per_unit_cues'])
            currency_raw = m.group('cur')
            currency = self.currency_map.get(str(currency_raw).lower())
            if has_per_unit_cue:
                items.append({'quantity': qty, 'product_name': prod, 'total_amount': None, 'unit_price': amt, 'currency': currency})
            else:
                items.append({'quantity': qty, 'product_name': prod, 'total_amount': amt, 'unit_price': None, 'currency': currency})
            return items

        # 2b) Quantity-Product-Currency-Price (Shona/Zimbabwe style): "3 bread R10 imwe"
        # e.g. "5 salt $5 imwe" => qty=5, prod=salt, price=5, per_unit_cue present
        m = re.search(r'(?P<qty>\d+)\s+(?P<prod>[a-zA-Z]+)\s+(?P<cur>\$|r|rand|z?ar|usd)\s*(?P<amount>\d+(?:\.\d{1,2})?)', lower, re.IGNORECASE)
        if m:
            qty = int(m.group('qty'))
            prod = self._normalize_product_name(m.group('prod'))
            if prod:  # only if product is valid
                amt = float(m.group('amount'))
                currency_raw = m.group('cur')
                currency = self.currency_map.get(str(currency_raw).lower())
                ctx = lower
                has_per_unit_cue = any(cue in ctx for cue in self.training_data['slots']['unit_price']['per_unit_cues'])
                item_dict = {
                    'quantity': qty,
                    'product_name': prod,
                    'currency': currency
                }
                if has_per_unit_cue:
                    item_dict['unit_price'] = amt
                    item_dict['total_amount'] = None
                else:
                    item_dict['unit_price'] = None
                    item_dict['total_amount'] = amt
                items.append(item_dict)
                return items

        # 3) Shorthand: qty product price (treat price as unit price by convention)
        # e.g. "3 coke 2", "5 sugar 8", "2 bread 1", "4 eggs @2"
        m = re.finditer(r'(?P<qty>\d+)\s*(?:x|times)?\s*(?P<prod>[a-zA-Z]+)\s*(?:@|x|at|\s)\s*(?P<price>\$?\d+(?:\.\d{1,2})?)', lower)
        found = False
        for match in m:
            found = True
            qty = int(match.group('qty'))
            prod = self._normalize_product_name(match.group('prod'))
            price_text = match.group('price')
            amt, cur = self._parse_price(price_text)
            # if '@' or 'each' present, treat as unit price
            seg = match.group(0)
            has_per_unit_cue = any(cue in seg or cue in lower for cue in self.training_data['slots']['unit_price']['per_unit_cues'])
            if has_per_unit_cue or re.search(r'@|\beach\b|\bper\b', seg):
                items.append({'quantity': qty, 'product_name': prod, 'total_amount': None, 'unit_price': amt, 'currency': cur})
            else:
                # shorthand: prefer unit price
                items.append({'quantity': qty, 'product_name': prod, 'total_amount': None, 'unit_price': amt, 'currency': cur})
        if found:
            return items

        # 4) Compact shorthand like '2drinks5' or '3coke2'
        m = re.finditer(r'(\d+)\s*([a-zA-Z]+)\s*(\d+(?:\.\d{1,2})?)', lower)
        for match in m:
            qty = int(match.group(1))
            prod = self._normalize_product_name(match.group(2))
            amt_text = match.group(3)
            amt, cur = self._parse_price(amt_text)
            items.append({'quantity': qty, 'product_name': prod, 'total_amount': None, 'unit_price': amt, 'currency': cur})
        if items:
            return items

        # 4b) Product-first patterns: 'Cola 2', 'bread 3 R50', 'coke 2 @ R20'
        m = re.finditer(r'(?P<prod>[a-zA-Z]+)\s+(?P<qty>\d+)(?:\s*(?P<cur>\$|r|rand|z?ar|usd)\s*(?P<price>\d+(?:\.\d{1,2})?))?', lower)
        product_examples = set([p.lower() for p in self.training_data['slots']['product_name'].get('examples', [])])
        typo_map = self.training_data.get('normalization', {}).get('common_typos', {})
        for match in m:
            raw_prod = match.group('prod')
            prod = self._normalize_product_name(raw_prod)
            # validate that this looks like a real product (in examples, synonyms, or typo map)
            prod_valid = prod and (prod in self.product_synonyms.values() or prod in product_examples or raw_prod.lower() in typo_map)
            if not prod_valid:
                continue
            qty = int(match.group('qty'))
            price_text = match.group('price')
            cur_raw = match.group('cur')
            amt = None
            cur = None
            if price_text:
                amt, cur = self._parse_price((cur_raw or '') + price_text)
            items.append({'quantity': qty, 'product_name': prod, 'total_amount': None if amt else None, 'unit_price': amt, 'currency': cur})
        if items:
            return items

        # 5) Fallback: previous quantity-first extraction (improved)
        quantity_pattern = r'(\d+|one|two|three|four|five|six|seven|eight|nine|ten)'
        quantities = []
        for match in re.finditer(quantity_pattern, lower):
            # skip numbers that are likely part of a price (preceded by currency symbol or letter)
            prev_segment = lower[max(0, match.start() - 4):match.start()]
            if re.search(r'(\$|\brand\b|\br\b|\bzar\b)', prev_segment, re.IGNORECASE):
                continue
            qty_text = match.group(1)
            qty = self._parse_number(qty_text)
            start_pos = match.end()
            quantities.append({'qty': qty, 'pos': match.start(), 'end_pos': start_pos})

        for i, qty_info in enumerate(quantities):
            start_pos = qty_info['end_pos']
            if i + 1 < len(quantities):
                end_pos = quantities[i + 1]['pos']
            else:
                end_pos = len(lower)
            text_after_qty = lower[start_pos:end_pos].strip()
            # extract product as first word
            product_word = re.match(r'([a-zA-Z]+)', text_after_qty)
            product = self._normalize_product_name(product_word.group(1)) if product_word else None
            item = {'quantity': qty_info['qty'], 'product_name': None, 'total_amount': None, 'unit_price': None, 'currency': None}
            # If the word after qty is a currency token (e.g., 'rand'), treat as currency not product
            if product_word:
                pw = product_word.group(1).lower()
                currency_tokens = set(k.lower() for k in self.training_data.get('normalization', {}).get('currency_symbols', {}).keys())
                if pw in currency_tokens:
                    item['currency'] = self.currency_map.get(pw)
                else:
                    # accept normalized product if valid
                    if product:
                        item['product_name'] = product
            # If product not found after quantity, try a product word before the quantity (handles 'Cola 2' cases where we matched qty at end)
            if not item['product_name']:
                before_segment = lower[max(0, qty_info['pos'] - 20):qty_info['pos']]
                before_match = re.search(r'([a-zA-Z]+)\s*$', before_segment)
                if before_match:
                    candidate = before_match.group(1)
                    cand_norm = self._normalize_product_name(candidate)
                    # accept candidate only if it's a known product or typo
                    product_examples = set([p.lower() for p in self.training_data['slots']['product_name'].get('examples', [])])
                    typo_map = self.training_data.get('normalization', {}).get('common_typos', {})
                    if cand_norm and (cand_norm in self.product_synonyms.values() or cand_norm in product_examples or candidate.lower() in typo_map):
                        item['product_name'] = cand_norm
            # price in segment
            price_match = re.search(r'(?P<cur>\$|r|rand|z?ar|usd)?\s*(?P<amt>\d+(?:\.\d{1,2})?)', text_after_qty)
            if price_match:
                amt = float(price_match.group('amt'))
                seg = text_after_qty
                has_per_unit_cue = any(cue in seg for cue in self.training_data['slots']['unit_price']['per_unit_cues']) or '@' in seg
                if has_per_unit_cue:
                    item['unit_price'] = amt
                else:
                    item['total_amount'] = amt
                cur = price_match.group('cur')
                if cur:
                    cur_norm = self.currency_map.get(str(cur).lower())
                    item['currency'] = cur_norm
            items.append(item)

        return items

    def _extract_quantity(self, message: str) -> Optional[int]:
        """Extract quantity from message."""
        # Look for number patterns: 3, three, 3x, x3
        patterns = [
            r'(\d+)\s*x\b',  # 3x
            r'\bx\s*(\d+)',  # x3
            r'(\d+)',  # plain number
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        # Try word numbers
        for word, digit in self.training_data["normalization"]["number_words"].items():
            if re.search(rf'\b{word}\b', message, re.IGNORECASE):
                return digit
        
        return None

    def _extract_product_name(self, message: str) -> Optional[str]:
        """Extract and normalize product name from message."""
        msg_lower = message.lower()
        
        # Sort by length descending to match longer product names first
        synonyms_sorted = sorted(self.product_synonyms.keys(), key=len, reverse=True)
        
        for syn in synonyms_sorted:
            if syn in msg_lower:
                canonical = self.product_synonyms[syn]
                return canonical
        
        return None

    def _extract_prices(self, message: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """
        Extract prices from message.
        Returns: (total_amount, unit_price, currency)
        """
        total_amount = None
        unit_price = None
        currency = None
        
        # Look for currency patterns: R20, R 20, 20 rand, $20, etc.
        price_patterns = [
            r'(?:R|rand|\$|USD|usd)\s*(\d+(?:\.\d{2})?)',  # R20, $20, etc.
            r'(\d+(?:\.\d{2})?)\s*(?:rand|r|\$)',  # 20 rand, 20 R
            r'(\d+(?:\.\d{2})?)\s*(?:each|per|apiece)',  # 20 each
        ]
        
        matches = []
        for pattern in price_patterns:
            for match in re.finditer(pattern, message, re.IGNORECASE):
                amount = float(match.group(1))
                matches.append(amount)
        
        if matches:
            # If multiple prices, assume first is unit, last is total (or vice versa)
            total_amount = matches[-1]
            if len(matches) > 1:
                unit_price = matches[0]
        
        # Extract currency
        currency_match = re.search(r'(R|rand|ZAR|USD|\$|ZWL)', message, re.IGNORECASE)
        if currency_match:
            currency_raw = currency_match.group(1)
            currency = self.currency_map.get(str(currency_raw).lower())
        
        return total_amount, unit_price, currency

    def _extract_payment_method(self, message: str) -> Optional[str]:
        """Extract payment method from message."""
        msg_lower = message.lower()
        
        for keyword, method in self.payment_keywords.items():
            if keyword in msg_lower:
                return method
        
        return None

    def _extract_time_reference(self, message: str) -> Optional[str]:
        """Extract time reference from message."""
        msg_lower = message.lower()
        
        # Check preset values
        for preset in self.training_data["slots"]["time_reference"]["preset_values"]:
            if preset in msg_lower:
                return preset
        
        # Check patterns: "from X to Y", "since X", "last N hours/days"
        if re.search(r'last\s+(\d+)\s+(hour|day)', msg_lower):
            match = re.search(r'last\s+(\d+)\s+(hour|day)', msg_lower)
            count, unit = match.groups()
            return f"last_{count}_{unit}s"
        
        if re.search(r'from\s+(\d{1,2})\s*(?:am|pm|:)', msg_lower):
            return "specific_time_range"
        
        return "today"  # Default

    def _extract_closing_time(self, message: str) -> Optional[str]:
        """Extract closing time from message."""
        # Look for time patterns: 8, 8 PM, 8:00, 17:00, etc.
        time_patterns = [
            r'(?:at|close[sd]?\s+(?:at)?)\s*(\d{1,2}):?(\d{2})?\s*(am|pm)?',
            r'(\d{1,2})\s*(am|pm)?',
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                hour, minute, meridiem = match.groups() if len(match.groups()) >= 3 else (*match.groups(), None)
                minute = minute or "00"
                return f"{hour.zfill(2)}:{minute}"
        
        return None

    def _normalize_product_name(self, product: str) -> str:
        """Normalize product name using synonym map."""
        if not product:
            return None
        
        product_lower = product.lower().strip()
        # If this token is a currency token, do not treat as product
        currency_tokens = set(k.lower() for k in self.training_data.get('normalization', {}).get('currency_symbols', {}).keys())
        if product_lower in currency_tokens:
            return None
        # Direct mapping
        if product_lower in self.product_synonyms:
            return self.product_synonyms[product_lower]

        # Try simple singularization (strip trailing 's')
        if product_lower.endswith('s'):
            singular = product_lower[:-1]
            if singular in self.product_synonyms:
                return self.product_synonyms[singular]

        # Check common typos map
        typo_map = self.training_data.get('normalization', {}).get('common_typos', {})
        if product_lower in typo_map:
            mapped = typo_map[product_lower]
            return self.product_synonyms.get(mapped.lower(), mapped.lower())

        return product_lower

    def _parse_number(self, text: str) -> Optional[int]:
        """Convert text number to int."""
        text_lower = text.lower().strip()
        
        if text_lower.isdigit():
            return int(text_lower)
        
        return self.training_data["normalization"]["number_words"].get(text_lower)

    def _parse_price(self, text: str) -> Tuple[Optional[float], Optional[str]]:
        """Parse price text to (amount, currency)."""
        # Extract amount
        amount_match = re.search(r'(\d+(?:\.\d{2})?)', text)
        amount = float(amount_match.group(1)) if amount_match else None
        
        # Extract currency
        currency_match = re.search(r'(R|rand|ZAR|USD|\$|ZWL)', text, re.IGNORECASE)
        currency = None
        if currency_match:
            currency_raw = currency_match.group(1)
            currency = self.currency_map.get(str(currency_raw).lower())
        
        return amount, currency

    def _string_similarity(self, s1: str, s2: str) -> float:
        """Compute similarity between two strings (0 to 1)."""
        return SequenceMatcher(None, s1, s2).ratio()

    def _fuzzy_match(self, pattern: str, text: str, threshold: float = 0.7) -> bool:
        """Fuzzy match pattern in text."""
        if pattern in text:
            return True
        return self._string_similarity(pattern, text) > threshold

    def _determine_clarifications(self, intent_id: str, slots: ParsedSlots) -> List[str]:
        """Determine which clarifications are needed."""
        clarifications = []
        
        if intent_id == "sales.record":
            if not slots.product_name and not slots.raw_items:
                clarifications.append("Which product did you sell?")
            # if raw_items exist but first item lacks product, ask product
            if slots.raw_items and any(not it.get('product_name') for it in slots.raw_items):
                clarifications.append("Which product did you sell?")
            if not slots.quantity and not slots.raw_items:
                clarifications.append("How many?")
            if slots.quantity and not slots.product_name:
                clarifications.append("Which product did you sell?")
            if slots.quantity and slots.unit_price and slots.total_amount:
                # Ambiguous: clarify which is which
                clarifications.append(
                    f"Clarify: R{slots.total_amount} total or R{slots.unit_price} each?"
                )
        
        elif intent_id == "report.daily_summary":
            if not slots.time_reference:
                clarifications.append("Which time period - today, yesterday, or specific hours?")
        
        return clarifications
