from django.contrib import admin

from apps.sales.models import Sale, SaleItem


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 1
    readonly_fields = ["line_total_display"]
    autocomplete_fields = ["product"]

    def line_total_display(self, obj):
        if obj.pk and obj.sale and obj.sale.company:
            return obj.sale.company.format_price(obj.line_total)
        elif obj.pk:
            return f"{obj.line_total:.2f}"
        return "-"

    line_total_display.short_description = "Line Total"


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["id", "company", "sale_timestamp", "total_amount", "item_count", "whatsapp_message_id"]
    list_filter = ["company", "sale_timestamp"]
    search_fields = ["id", "whatsapp_message_id"]
    date_hierarchy = "sale_timestamp"
    inlines = [SaleItemInline]
    readonly_fields = ["total_amount"]
    autocomplete_fields = ["company"]

    def item_count(self, obj):
        return obj.items.count()

    item_count.short_description = "Items"

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ["sale", "product", "quantity", "unit_price", "line_total_display"]
    list_filter = ["sale__sale_timestamp", "product"]
    autocomplete_fields = ["product", "sale"]

    def line_total_display(self, obj):
        if obj.sale and obj.sale.company:
            return obj.sale.company.format_price(obj.line_total)
        return f"{obj.line_total:.2f}"

    line_total_display.short_description = "Line Total"

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
