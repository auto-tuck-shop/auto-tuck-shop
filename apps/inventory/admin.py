from django.contrib import admin

from apps.inventory.models import InventoryAdjustment


@admin.register(InventoryAdjustment)
class InventoryAdjustmentAdmin(admin.ModelAdmin):
    list_display = [
        "product",
        "quantity_delta_display",
        "reason",
        "timestamp",
    ]
    list_filter = ["reason", "timestamp", "product"]
    search_fields = ["product__name", "notes"]
    date_hierarchy = "timestamp"
    autocomplete_fields = ["product"]

    def quantity_delta_display(self, obj):
        sign = "+" if obj.quantity_delta > 0 else ""
        return f"{sign}{obj.quantity_delta}"

    quantity_delta_display.short_description = "Quantity Change"
