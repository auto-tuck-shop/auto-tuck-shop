"""Prompt templates for OpenRouter LLM interactions."""

from apps.catalog.models import Product

INTENT_DETECTION_SYSTEM_PROMPT = """You are a message intent classifier for a tuck shop (small retail store) sales tracking system.

Your job is to determine the intent of incoming messages:
1. "sale" - The message is about recording a sale (e.g., "2 cokes, 1 chips", "sold 3 waters")
2. "add_assistant" - The user wants to add an assistant/team member (e.g., "add assistant +27821234567", "add my friend 0821234567")
3. "other" - The message doesn't fit either category

For "add_assistant" intent, also extract the phone number from the message if present.
Phone numbers may be in various formats: +27821234567, 0821234567, 082-123-4567, 082 123 4567, etc.
Normalize the phone number to include the country code (assume South Africa +27 if no country code provided).

Respond with a JSON object in this exact format:
{
    "intent": "sale" | "add_assistant" | "other",
    "phone_number": "+27821234567" or null (only for add_assistant intent),
    "confidence": 0.0 to 1.0
}"""


def build_intent_detection_prompt(message: str) -> list[dict[str, str]]:
    """
    Build the message list for detecting message intent.

    Args:
        message: The raw message to classify

    Returns:
        List of messages for the chat completion
    """
    return [
        {"role": "system", "content": INTENT_DETECTION_SYSTEM_PROMPT},
        {"role": "user", "content": f"Classify this message:\n{message}"},
    ]


SALE_PARSING_SYSTEM_PROMPT = """You are a sales data extraction assistant for a tuck shop (small retail store).
Your job is to parse informal sales messages and extract structured data about what was sold.

You will receive a message describing one or more sales, and a list of available products.
Extract the items sold, quantities, and match them to the closest product names from the available list.

IMPORTANT RULES:
1. Match product names flexibly - "coke", "coca cola", "coca-cola" should all match "Coca-Cola 500ml"
2. If quantity is not specified, assume 1
3. If a product cannot be matched to the available list, still include it with your best guess at the intended name
4. Prices mentioned in the message are informational - extract them if present but they're optional
5. Detect the currency used from symbols or context:
   - $ or USD or dollars = "USD"
   - ZiG or ZWG or Zimbabwe Gold = "ZWG"
   - R or ZAR or rand = "ZAR"
   - P or BWP or pula = "BWP"
   - € or EUR or euro = "EUR"
   - £ or GBP or pound = "GBP"

Respond with a JSON object in this exact format:
{
    "items": [
        {
            "product_name": "exact product name from available list or best guess",
            "quantity": 1,
            "unit_price": 10.00 or null if not mentioned
        }
    ],
    "currency": "USD" or "ZWG" or "ZAR" or "BWP" or "EUR" or "GBP" or null if no currency detected,
    "notes": "any relevant notes about the parsing, or null"
}"""


def build_sale_parsing_prompt(message: str, products: list[Product] | None = None) -> list[dict[str, str]]:
    """
    Build the message list for parsing a sale message.

    Args:
        message: The raw message to parse
        products: Optional list of available products (fetched if not provided)

    Returns:
        List of messages for the chat completion
    """
    if products is None:
        products = list(Product.objects.filter(active=True))

    product_list = "\n".join(f"- {p.name}" for p in products)

    user_content = f"""Available products:
{product_list}

Sales message to parse:
{message}

Extract the items sold as JSON."""

    return [
        {"role": "system", "content": SALE_PARSING_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
