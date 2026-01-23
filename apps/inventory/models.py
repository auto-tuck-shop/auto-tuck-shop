from django.db import models
from django.utils import timezone

from apps.catalog.models import Product


class InventoryAdjustment(models.Model):
    """Inventory adjustment record for stock changes."""

    class Reason(models.TextChoices):
        PURCHASE = "purchase", "Purchase/Restock"
        RETURN = "return", "Customer Return"
        DAMAGE = "damage", "Damaged/Expired"
        CORRECTION = "correction", "Stock Correction"
        INITIAL = "initial", "Initial Stock"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="inventory_adjustments",
    )
    quantity_delta = models.IntegerField(
        help_text="Positive for additions, negative for removals"
    )
    reason = models.CharField(
        max_length=20,
        choices=Reason.choices,
        default=Reason.CORRECTION,
    )
    timestamp = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        sign = "+" if self.quantity_delta > 0 else ""
        return f"{self.product.name}: {sign}{self.quantity_delta} ({self.get_reason_display()})"
