from django.contrib import admin

from apps.catalog.models import Category, Product, ProductPrice


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "product_count"]
    list_filter = ["company"]
    search_fields = ["name"]
    autocomplete_fields = ["company"]

    def product_count(self, obj):
        return obj.category_products.count()

    product_count.short_description = "Products"


class ProductPriceInline(admin.TabularInline):
    model = ProductPrice
    extra = 1
    ordering = ["-effective_from"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "company",
        "sku",
        "category",
        "cost",
        "display_current_price",
        "display_current_stock",
        "active",
    ]
    list_filter = ["company", "active", "category"]
    search_fields = ["name", "sku"]
    autocomplete_fields = ["company", "category"]
    inlines = [ProductPriceInline]

    def display_current_price(self, obj):
        price = obj.current_price
        if price is None:
            return "-"
        if obj.company:
            return obj.company.format_price(price)
        return f"{price:.2f}"

    display_current_price.short_description = "Current Price"

    def display_current_stock(self, obj):
        return obj.current_stock

    display_current_stock.short_description = "Stock"


@admin.register(ProductPrice)
class ProductPriceAdmin(admin.ModelAdmin):
    list_display = ["product", "price", "effective_from"]
    list_filter = ["product"]
    date_hierarchy = "effective_from"
