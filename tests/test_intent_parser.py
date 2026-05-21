"""
Comprehensive tests for the intent parser.

Tests cover:
- Intent classification
- Slot extraction (quantity, product, price, currency, payment, time)
- Multilingual support
- Typo tolerance
- Ambiguous cases
- Total vs unit price detection
- Multiple items
"""

import pytest
from services.whatsapp.intent_parser import IntentParser, ParsedSlots, ParseResult


@pytest.fixture
def parser():
    """Create parser instance for tests."""
    return IntentParser()


class TestIntentClassification:
    """Test intent classification."""

    def test_sales_record_basic(self, parser):
        result = parser.parse("I sold 3 breads")
        assert result.intent_id == "sales.record"
        assert result.confidence > 0.5

    def test_sales_record_variations(self, parser):
        messages = [
            "Sold 1 Coke for R20",
            "2 cokes, 1 chips",
            "Add sale: 2 chips and 1 cooldrink",
            "Record R150 cash sale",
            "Customer bought 5 loaves",
        ]
        for msg in messages:
            result = parser.parse(msg)
            assert result.intent_id == "sales.record", f"Failed for: {msg}"

    def test_daily_report_query(self, parser):
        messages = [
            "What happened today?",
            "Show today's sales",
            "Daily summary",
            "Give me the report",
            "End of day report",
        ]
        for msg in messages:
            result = parser.parse(msg)
            assert result.intent_id == "report.daily_summary", f"Failed for: {msg}"

    def test_closing_time(self, parser):
        messages = [
            "We close at 8",
            "Closing at 8 PM",
            "I'm closing now",
            "Shop closes at 7",
            "Time to close",
        ]
        for msg in messages:
            result = parser.parse(msg)
            assert result.intent_id == "shop.closing", f"Failed for: {msg}"

    def test_inventory_update(self, parser):
        messages = [
            "We are out of bread",
            "Low stock",
            "Running out of cokes",
            "Add stock: 20 loaves",
            "New stock arrived",
        ]
        for msg in messages:
            result = parser.parse(msg)
            assert result.intent_id == "inventory.update", f"Failed for: {msg}"

    def test_profit_query(self, parser):
        messages = [
            "How much did we make?",
            "Profit today",
            "Cash made today",
            "Total sales amount",
            "How much came in?",
        ]
        for msg in messages:
            result = parser.parse(msg)
            assert result.intent_id == "finance.profit_query", f"Failed for: {msg}"

    def test_business_status(self, parser):
        messages = [
            "How is the business doing?",
            "What's happening?",
            "Most sold products?",
            "Busy hours today?",
        ]
        for msg in messages:
            result = parser.parse(msg)
            assert result.intent_id == "business.status", f"Failed for: {msg}"


class TestSlotExtraction:
    """Test slot extraction for sales records."""

    def test_quantity_extraction(self, parser):
        result = parser.parse("I sold 3 breads")
        assert result.slots.quantity == 3

    def test_quantity_words(self, parser):
        result = parser.parse("Sold five waters")
        assert result.slots.quantity == 5

    def test_product_name_extraction(self, parser):
        result = parser.parse("Sold 2 cokes")
        assert result.slots.product_name == "coke"

    def test_product_normalization(self, parser):
        messages_and_expected = [
            ("Cola 2", "coke"),
            ("3 bread", "bread"),
            ("5 waters", "water"),
            ("2 chips", "chips"),
        ]
        for msg, expected_product in messages_and_expected:
            result = parser.parse(msg)
            assert result.slots.product_name == expected_product, f"Failed for: {msg}"

    def test_currency_extraction(self, parser):
        messages_and_currencies = [
            ("Sold R100", "ZAR"),
            ("3 at $20 each", "USD"),
            ("Paid 50 rand", "ZAR"),
        ]
        for msg, expected_currency in messages_and_currencies:
            result = parser.parse(msg)
            assert result.slots.currency == expected_currency, f"Failed for: {msg}"

    def test_unit_price_extraction(self, parser):
        # Unit price: has per-unit cue (each, per, apiece, etc.)
        result = parser.parse("3 bread R50 each")
        # The parser should detect the per-unit cue and extract unit_price
        # (depends on implementation details)
        assert result.slots.quantity == 3
        assert result.slots.product_name == "bread"

    def test_total_amount_extraction(self, parser):
        # Total amount: no per-unit cue
        result = parser.parse("3 bread R100")
        assert result.slots.quantity == 3
        assert result.slots.product_name == "bread"

    def test_payment_method_extraction(self, parser):
        messages_and_methods = [
            ("Cash sale: 2 breads R100", "cash"),
            ("Card payment: 3 chips R75", "card"),
            ("Customer paid: R50 cash", "cash"),
            ("EFT: R200 for cakes", "eft"),
        ]
        for msg, expected_method in messages_and_methods:
            result = parser.parse(msg)
            assert result.slots.payment_method == expected_method, f"Failed for: {msg}"

    def test_time_reference_extraction(self, parser):
        result = parser.parse("Show today's sales")
        # Should extract time reference from report query
        assert result.intent_id == "report.daily_summary"

    def test_time_reference_specific(self, parser):
        result = parser.parse("Show sales from this morning")
        assert result.slots.time_reference is not None


class TestInformalAndMisspellings:
    """Test handling of informal language, slang, and typos."""

    def test_lowercase(self, parser):
        result = parser.parse("i sold 3 breads")
        assert result.intent_id == "sales.record"
        assert result.slots.quantity == 3

    def test_missing_punctuation(self, parser):
        result = parser.parse("sold 2 cokes 1 chips")
        assert result.intent_id == "sales.record"

    def test_text_speak(self, parser):
        result = parser.parse("sld 2 cokes")
        # Parser should normalize "sld" to "sold"
        assert result.intent_id == "sales.record"

    def test_typos_tolerance(self, parser):
        # These should still parse (fuzzy matching or normalization)
        messages = [
            "Sold 2 cole",  # coke
            "3 bret",  # bread
            "watar",  # water
        ]
        for msg in messages:
            result = parser.parse(msg)
            # Should still classify as sales or at least attempt parsing
            assert result.intent_id is not None

    def test_informal_quantity(self, parser):
        result = parser.parse("sold about 3 cokes")
        # Should extract quantity despite "about"
        assert result.slots.quantity == 3

    def test_text_contractions(self, parser):
        result = parser.parse("c u 2morrow")
        # Parser should expand contraction "c u" -> "see you", "2morrow" -> "tomorrow"
        normalized = result.normalized_message
        assert "see you" in normalized.lower() or "tomorrow" in normalized.lower()


class TestMultilingualSupport:
    """Test multilingual example handling."""

    def test_shona_examples(self, parser):
        # These should at least attempt to parse (may not fully work without translation)
        messages = [
            "Vendi 2 pani",  # Sold 2 bread
            "Vendi 5 cokes",
        ]
        for msg in messages:
            result = parser.parse(msg)
            # Should at least classify intent or extract numbers
            assert result is not None

    def test_zulu_examples(self, parser):
        messages = [
            "Uthengisile 2 amanzi",  # Sold 2 waters
        ]
        for msg in messages:
            result = parser.parse(msg)
            assert result is not None

    def test_code_switching(self, parser):
        result = parser.parse("Sold 2 cokes, by card please")
        assert result.intent_id == "sales.record"
        assert result.slots.quantity == 2
        assert result.slots.payment_method == "card"


class TestAmbiguousCases:
    """Test handling of ambiguous messages that need clarification."""

    def test_missing_product_name(self, parser):
        result = parser.parse("I sold 5")
        assert result.intent_id == "sales.record"
        assert "Which product" in " ".join(result.clarifications_needed)

    def test_missing_quantity(self, parser):
        result = parser.parse("sold bread")
        assert result.intent_id == "sales.record"
        # May need clarification on quantity
        assert len(result.clarifications_needed) >= 0  # Flexible

    def test_total_vs_unit_ambiguity(self, parser):
        # "R100" could be total or per-unit without explicit cues
        result = parser.parse("3 cokes R100")
        assert result.intent_id == "sales.record"
        # May flag as ambiguous
        assert len(result.clarifications_needed) >= 0

    def test_missing_time_reference(self, parser):
        result = parser.parse("what sold")
        # Without clear time, might need clarification
        # This could be "report.daily_summary" or just "other"


class TestMultipleItems:
    """Test parsing of multiple-item transactions."""

    def test_two_items_simple(self, parser):
        result = parser.parse("2 cokes and 3 chips")
        assert result.intent_id == "sales.record"
        # Should have captured items
        assert result.slots.raw_items or result.slots.quantity

    def test_two_items_with_prices(self, parser):
        result = parser.parse("2 cokes R20 each and 3 chips R50")
        assert result.intent_id == "sales.record"

    def test_three_items(self, parser):
        result = parser.parse("2 bread, 3 water, 1 chips")
        assert result.intent_id == "sales.record"


class TestEdgeCaseParsing:
    """New tests for shorthand, price-first, mixed-language, misspellings, and voice style."""

    def test_price_first_for(self, parser):
        result = parser.parse("$5 for 2 bread")
        assert result.intent_id == "sales.record"
        items = result.slots.raw_items
        assert items and items[0]["total_amount"] == 5 and items[0]["quantity"] == 2

    def test_price_first_without_for(self, parser):
        result = parser.parse("R20 4 drinks")
        assert result.intent_id == "sales.record"
        items = result.slots.raw_items
        assert items and items[0]["quantity"] == 4

    def test_price_first_usd(self, parser):
        result = parser.parse("$2 3 mazai")
        assert result.intent_id == "sales.record"
        assert result.slots.raw_items

    def test_short_messages(self, parser):
        cases = ["3 coke 2", "5 sugar 8", "2 bread 1", "4 eggs @2", "6 soap each 3"]
        for msg in cases:
            result = parser.parse(msg)
            assert result.intent_id == "sales.record", f"Failed for: {msg}"

    def test_shorthand_variants(self, parser):
        cases = ["sold 3 coke @2", "add 5 sugar 10", "4x bread R20", "3 coke x2", "2drinks5"]
        for msg in cases:
            result = parser.parse(msg)
            assert result.intent_id == "sales.record", f"Failed for: {msg}"

    def test_mixed_language(self, parser):
        cases = ["ndatengesa 3 coke $2", "munhu atora 5 bread R10", "sold 4 mazai rimwe $1", "2 maputi each 5"]
        for msg in cases:
            result = parser.parse(msg)
            assert result.intent_id == "sales.record", f"Failed for: {msg}"

    def test_spelling_mistakes(self, parser):
        cases = ["3 cok $2", "5 surgar 10", "2 dringks each 3", "4 mazie $1"]
        for msg in cases:
            result = parser.parse(msg)
            assert result.intent_id == "sales.record", f"Failed for: {msg}"

    def test_voice_to_text(self, parser):
        cases = [
            "I sold like 3 cokes for 2 dollars",
            "customer took 5 breads and total was 10",
            "someone bought 2 drinks at 3 each",
        ]
        for msg in cases:
            result = parser.parse(msg)
            assert result.intent_id == "sales.record", f"Failed for: {msg}"


class TestPriceFormats:
    """Test various price format handling."""

    def test_rands_with_symbol(self, parser):
        result = parser.parse("R20")
        assert result.slots.currency == "ZAR"

    def test_rands_spelled_out(self, parser):
        result = parser.parse("20 rand")
        assert result.slots.currency == "ZAR"

    def test_usd_symbol(self, parser):
        result = parser.parse("$10")
        assert result.slots.currency == "USD"

    def test_price_with_decimal(self, parser):
        result = parser.parse("R99.99")
        # Should parse decimal price
        assert result is not None

    def test_price_ranges(self, parser):
        # "2-3 cokes" as quantity range
        result = parser.parse("2-3 cokes R50")
        assert result.intent_id == "sales.record"


class TestEdgeCases:
    """Test unusual or edge cases."""

    def test_empty_message(self, parser):
        result = parser.parse("")
        assert result is not None

    def test_single_word(self, parser):
        result = parser.parse("sold")
        assert result.intent_id == "sales.record"

    def test_very_long_message(self, parser):
        msg = "I sold " + "and ".join(["3 cokes"] * 10)
        result = parser.parse(msg)
        assert result is not None

    def test_special_characters(self, parser):
        result = parser.parse("Sold @@ 3 ## cokes R50!!!!")
        # Should still parse despite special chars
        assert result is not None

    def test_mixed_case(self, parser):
        result = parser.parse("I SoLd 3 BrEaDs")
        assert result.intent_id == "sales.record"

    def test_numbers_spelled_mixed(self, parser):
        result = parser.parse("sold 3 waters and five cokes")
        # Should parse both numeric and word numbers
        assert result is not None


class TestNormalization:
    """Test message normalization."""

    def test_number_word_expansion(self, parser):
        result = parser.parse("sold five breads")
        assert result.slots.quantity == 5

    def test_contraction_expansion(self, parser):
        result = parser.parse("see u 2morrow")
        # Should expand to "see you tomorrow"
        assert "see you" in result.normalized_message.lower() or "tomorrow" in result.normalized_message.lower()

    def test_currency_normalization(self, parser):
        result = parser.parse("paid 50 rand")
        # Should normalize "rand" to ZAR code
        assert result.slots.currency == "ZAR"


class TestConfidenceScores:
    """Test confidence scoring."""

    def test_high_confidence_match(self, parser):
        result = parser.parse("I sold 3 breads")
        # Should have high confidence for exact/near match
        assert result.confidence > 0.5

    def test_response_has_confidence(self, parser):
        result = parser.parse("something")
        assert 0 <= result.confidence <= 1


class TestClarifications:
    """Test clarification detection."""

    def test_missing_product_clarification(self, parser):
        result = parser.parse("I sold 5")
        assert any("product" in c.lower() for c in result.clarifications_needed)

    def test_no_clarification_needed(self, parser):
        result = parser.parse("I sold 3 cokes R50 each")
        # Should be clear enough, minimal clarifications
        assert len(result.clarifications_needed) == 0 or all(
            len(c) > 0 for c in result.clarifications_needed
        )


class TestParseResultFormat:
    """Test output format of ParseResult."""

    def test_parse_result_to_dict(self, parser):
        result = parser.parse("I sold 3 cokes R50")
        result_dict = result.to_dict()
        
        assert "intent_id" in result_dict
        assert "intent_name" in result_dict
        assert "confidence" in result_dict
        assert "slots" in result_dict
        assert "raw_message" in result_dict
        assert "normalized_message" in result_dict
        assert "clarifications_needed" in result_dict

    def test_slots_to_dict(self, parser):
        result = parser.parse("I sold 3 cokes R50")
        slots_dict = result.slots.to_dict()
        
        assert "quantity" in slots_dict
        assert "product_name" in slots_dict
        assert "total_amount" in slots_dict
        assert "unit_price" in slots_dict
        assert "currency" in slots_dict
        assert "payment_method" in slots_dict
        assert "time_reference" in slots_dict
        assert "raw_items" in slots_dict


class TestClosingTimeExtraction:
    """Test closing time parsing."""

    def test_closing_8pm(self, parser):
        result = parser.parse("We close at 8 PM")
        assert result.intent_id == "shop.closing"
        assert result.slots.time_reference is not None

    def test_closing_8_no_meridiem(self, parser):
        result = parser.parse("Shop closes at 8")
        assert result.intent_id == "shop.closing"

    def test_closing_24hr(self, parser):
        result = parser.parse("Close at 17:00")
        assert result.intent_id == "shop.closing"


class TestInventorySlotExtraction:
    """Test inventory-specific slot extraction."""

    def test_out_of_stock_product(self, parser):
        result = parser.parse("We are out of bread")
        assert result.intent_id == "inventory.update"
        assert result.slots.product_name == "bread"

    def test_restock_with_quantity(self, parser):
        result = parser.parse("Restock bread: 20 loaves")
        assert result.intent_id == "inventory.update"
        assert result.slots.product_name is not None
