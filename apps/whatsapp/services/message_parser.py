"""Service for parsing sale messages using OpenRouter LLM."""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from asgiref.sync import sync_to_async

from apps.catalog.models import Product
from apps.sales.services import ParsedSaleItem
from services.openrouter import OpenRouterClient, build_intent_detection_prompt, build_sale_parsing_prompt

logger = logging.getLogger(__name__)


@dataclass
class ParsedSaleMessage:
    """Result of parsing a sale message."""
    items: list[ParsedSaleItem]
    currency: str | None = None


async def detect_message_intent(message: str) -> dict[str, Any]:
    """
    Detect the intent of a message using the OpenRouter LLM.

    Args:
        message: The raw message text to classify

    Returns:
        Dict with intent type, optional phone_number, and confidence
    """
    messages = build_intent_detection_prompt(message)
    client = OpenRouterClient()

    try:
        response = await client.parse_json_response(messages)
        return response
    except Exception as e:
        logger.error(f"Failed to detect message intent: {e}")
        # Default to sale intent if detection fails
        return {"intent": "sale", "phone_number": None, "confidence": 0.5}


@sync_to_async
def _get_active_products():
    """Fetch active products (sync wrapper for async context)."""
    return list(Product.objects.filter(active=True))


async def parse_sale_message(message: str) -> ParsedSaleMessage:
    """
    Parse a sale message using the OpenRouter LLM.

    Args:
        message: The raw message text to parse

    Returns:
        ParsedSaleMessage with items and detected currency
    """
    products = await _get_active_products()
    messages = build_sale_parsing_prompt(message, products)

    client = OpenRouterClient()

    try:
        response = await client.parse_json_response(messages)
    except Exception as e:
        logger.error(f"Failed to parse sale message: {e}")
        return ParsedSaleMessage(items=[])

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
                )
            )
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping invalid item {item}: {e}")
            continue

    if response.get("notes"):
        logger.info(f"LLM parsing notes: {response['notes']}")

    # Extract detected currency
    currency = response.get("currency")
    if currency and currency not in ("USD", "ZWG", "ZAR", "BWP", "EUR", "GBP"):
        logger.warning(f"Unknown currency detected: {currency}, ignoring")
        currency = None

    return ParsedSaleMessage(items=parsed_items, currency=currency)
