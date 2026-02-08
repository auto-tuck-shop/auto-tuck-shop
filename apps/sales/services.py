from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

from django.db import transaction

from apps.catalog.models import Product, ProductPrice
from apps.sales.models import Sale, SaleItem

if TYPE_CHECKING:
    from apps.core.models import Company


class ParsedSaleItem(TypedDict):
    product_name: str
    quantity: int
    unit_price: Decimal | None


class SaleCreationResult(TypedDict):
    sale: Sale
    unmatched_items: list[str]


def find_product_by_name(name: str, company: Company) -> Product | None:
    """Find a product by name (case-insensitive exact match), scoped to company."""
    return Product.objects.filter(
        active=True, company=company
    ).filter(name__iexact=name).first()


@transaction.atomic
def create_sale_from_parsed_items(
    items: list[ParsedSaleItem],
    whatsapp_message_id: str | None = None,
    company: Company = None,
    currency: str | None = None,
) -> SaleCreationResult:
    """
    Create a sale from parsed sale items.

    Args:
        items: List of parsed sale items
        whatsapp_message_id: Optional WhatsApp message ID
        company: The company this sale belongs to
        currency: The detected currency from the message

    Returns the created sale and a list of unmatched product names.
    """
    sale = Sale.objects.create(
        whatsapp_message_id=whatsapp_message_id,
        company=company,
    )
    unmatched_items: list[str] = []
    detected_currency = currency or "USD"

    for item in items:
        product = find_product_by_name(item["product_name"], company=company)

        if not product:
            # Create new product if not found
            product = Product.objects.create(
                name=item["product_name"],
                company=company,
            )
            # If a price is provided, set it as the initial price with currency
            if item.get("unit_price") is not None:
                ProductPrice.objects.create(
                    product=product,
                    price=item["unit_price"],
                    currency=detected_currency,
                )

        # Determine price and currency
        unit_price = item.get("unit_price")
        item_currency = None

        if unit_price is not None:
            # Price provided in message - use detected currency
            item_currency = detected_currency

            # Update stored price if different from current price
            current_price = product.current_price
            if current_price is None or unit_price != current_price:
                ProductPrice.objects.create(
                    product=product,
                    price=unit_price,
                    currency=detected_currency,
                )
        else:
            # Fall back to stored price with its currency
            price_with_currency = product.current_price_with_currency
            if price_with_currency:
                unit_price, item_currency = price_with_currency

        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=item["quantity"],
            unit_price=unit_price,
            currency=item_currency,
        )

    # Recalculate total
    sale.total_amount = sale.calculate_total()
    sale.save()

    return {"sale": sale, "unmatched_items": unmatched_items}
