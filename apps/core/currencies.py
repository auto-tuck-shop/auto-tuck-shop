"""
Currency configuration for multi-currency support.
Focused on currencies commonly used in Zimbabwe and Southern Africa.
"""

from decimal import Decimal
from typing import NamedTuple


class CurrencyInfo(NamedTuple):
    code: str
    name: str
    symbol: str
    decimal_places: int
    symbol_before: bool  # True if symbol comes before amount (e.g., $10), False if after


# Supported currencies with focus on Zimbabwe and Southern Africa
CURRENCIES = {
    "USD": CurrencyInfo(
        code="USD",
        name="US Dollar",
        symbol="$",
        decimal_places=2,
        symbol_before=True,
    ),
    "ZWG": CurrencyInfo(
        code="ZWG",
        name="Zimbabwe Gold",
        symbol="ZiG",
        decimal_places=2,
        symbol_before=False,
    ),
    "ZAR": CurrencyInfo(
        code="ZAR",
        name="South African Rand",
        symbol="R",
        decimal_places=2,
        symbol_before=True,
    ),
    "BWP": CurrencyInfo(
        code="BWP",
        name="Botswana Pula",
        symbol="P",
        decimal_places=2,
        symbol_before=True,
    ),
    "EUR": CurrencyInfo(
        code="EUR",
        name="Euro",
        symbol="\u20ac",
        decimal_places=2,
        symbol_before=True,
    ),
    "GBP": CurrencyInfo(
        code="GBP",
        name="British Pound",
        symbol="\u00a3",
        decimal_places=2,
        symbol_before=True,
    ),
}

# Default currency (USD is most common in Zimbabwe)
DEFAULT_CURRENCY = "USD"

# Currency choices for Django model fields
CURRENCY_CHOICES = [(code, f"{info.name} ({info.symbol})") for code, info in CURRENCIES.items()]


def get_currency_info(currency_code: str) -> CurrencyInfo:
    """Get currency info by code, falling back to default if not found."""
    return CURRENCIES.get(currency_code, CURRENCIES[DEFAULT_CURRENCY])


def format_price(amount: Decimal | float | int | None, currency_code: str = DEFAULT_CURRENCY) -> str:
    """
    Format a price amount with the appropriate currency symbol.

    Args:
        amount: The price amount to format
        currency_code: The currency code (e.g., 'USD', 'ZWG', 'ZAR')

    Returns:
        Formatted price string (e.g., '$10.00', '100.00 ZiG', 'R50.00')
    """
    if amount is None:
        return "-"

    currency = get_currency_info(currency_code)

    # Format the number with appropriate decimal places
    if isinstance(amount, Decimal):
        formatted_amount = f"{amount:.{currency.decimal_places}f}"
    else:
        formatted_amount = f"{Decimal(str(amount)):.{currency.decimal_places}f}"

    # Apply symbol positioning
    if currency.symbol_before:
        return f"{currency.symbol}{formatted_amount}"
    else:
        return f"{formatted_amount} {currency.symbol}"


def get_currency_symbol(currency_code: str = DEFAULT_CURRENCY) -> str:
    """Get just the symbol for a currency."""
    return get_currency_info(currency_code).symbol
