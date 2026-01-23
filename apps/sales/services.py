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


def find_product_by_name(name: str, company: Company | None = None) -> Product | None:
    """Find a product by name (case-insensitive, partial match), scoped to company."""
    queryset = Product.objects.filter(active=True)
    if company:
        queryset = queryset.filter(company=company)

    # Try exact match first
    product = queryset.filter(name__iexact=name).first()
    if product:
        return product

    # Try contains match
    product = queryset.filter(name__icontains=name).first()
    return product


@transaction.atomic
def create_sale_from_parsed_items(
    items: list[ParsedSaleItem],
    whatsapp_message_id: str | None = None,
    company: Company | None = None,
) -> SaleCreationResult:
    """
    Create a sale from parsed sale items.

    Args:
        items: List of parsed sale items
        whatsapp_message_id: Optional WhatsApp message ID
        company: The company this sale belongs to

    Returns the created sale and a list of unmatched product names.
    """
    sale = Sale.objects.create(
        whatsapp_message_id=whatsapp_message_id,
        company=company,
    )
    unmatched_items: list[str] = []

    for item in items:
        product = find_product_by_name(item["product_name"], company=company)

        if not product:
            # Create new product if not found
            product = Product.objects.create(
                name=item["product_name"],
                company=company,
            )
            # If a price is provided, set it as the initial price
            if item.get("unit_price") is not None:
                ProductPrice.objects.create(product=product, price=item["unit_price"])

        # Use provided price or current product price (can be None)
        unit_price = item.get("unit_price") or product.current_price

        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=item["quantity"],
            unit_price=unit_price,
        )

    # Recalculate total
    sale.total_amount = sale.calculate_total()
    sale.save()

    return {"sale": sale, "unmatched_items": unmatched_items}
