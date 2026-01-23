from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.utils import timezone

from apps.core.currencies import format_price


class Category(models.Model):
    """Product category for organizing items."""

    company = models.ForeignKey(
        "core.Company",
        on_delete=models.CASCADE,
        related_name="categories",
        null=True,  # Nullable for migration - will be required later
        blank=True,
    )
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    """Product in the tuck shop catalog."""

    company = models.ForeignKey(
        "core.Company",
        on_delete=models.CASCADE,
        related_name="products",
        null=True,  # Nullable for migration - will be required later
        blank=True,
    )
    sku = models.CharField(max_length=50, null=True, blank=True)
    name = models.CharField(max_length=200)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="category_products",
    )
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "sku"],
                name="unique_sku_per_company",
                condition=models.Q(sku__isnull=False),
            )
        ]

    def __str__(self):
        return self.name

    @property
    def current_price(self) -> Decimal | None:
        """Get the current effective price for this product."""
        price = (
            self.prices.filter(effective_from__lte=timezone.now())
            .order_by("-effective_from")
            .first()
        )
        return price.price if price else None

    @property
    def current_stock(self) -> int:
        """Calculate current stock from adjustments minus sales."""
        from apps.sales.models import SaleItem

        # Sum of inventory adjustments
        adjustments_total = (
            self.inventory_adjustments.aggregate(total=Sum("quantity_delta"))["total"]
            or 0
        )

        # Sum of sale items
        sales_total = (
            SaleItem.objects.filter(product=self).aggregate(total=Sum("quantity"))[
                "total"
            ]
            or 0
        )

        return adjustments_total - sales_total


class ProductPrice(models.Model):
    """Historical price tracking for products."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="prices",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    effective_from = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self):
        currency = self.product.company.currency if self.product.company else "USD"
        return f"{self.product.name} - {format_price(self.price, currency)} (from {self.effective_from.date()})"
