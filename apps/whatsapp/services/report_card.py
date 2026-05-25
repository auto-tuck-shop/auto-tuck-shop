"""Generate a WhatsApp-ready PNG stat card for daily business reports."""

from __future__ import annotations

import io
import logging
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from apps.whatsapp.services.business_reports import BusinessSnapshot
from apps.core.currencies import format_price

logger = logging.getLogger(__name__)

# Card dimensions (good for WhatsApp image preview on mobile)
WIDTH = 800
HEIGHT = 480

# Colour palette
BG_COLOR = (18, 24, 38)          # dark navy
ACCENT_COLOR = (74, 222, 128)    # green
RED_COLOR = (248, 113, 113)      # red/orange for negative delta
MUTED_COLOR = (148, 163, 184)    # slate grey
WHITE = (255, 255, 255)
GOLD = (250, 204, 21)            # star / best day highlight
CARD_BG = (30, 41, 59)          # slightly lighter card interior

PADDING = 40


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font at the given size, falling back to Pillow's default."""
    candidates: list[Path] = []

    # Try common system font locations on Linux (Fly.io) and Windows (dev)
    if bold:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/calibrib.ttf"),
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/calibri.ttf"),
        ]

    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue

    # Pillow built-in fallback (no size control, but always works)
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def generate_stat_card(snapshot: BusinessSnapshot, comparison: dict, shop_name: str = "") -> bytes:
    """
    Render a PNG stat card summarising the day's sales.

    Args:
        snapshot: Today's BusinessSnapshot.
        comparison: Output of build_comparison_context() — contains delta, is_best_day_this_week, etc.
        shop_name: Display name for the shop (falls back to "Your Shop").

    Returns:
        PNG image as bytes.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Fonts
    font_small = _load_font(18)
    font_medium = _load_font(24)
    font_large = _load_font(52, bold=True)
    font_xlarge = _load_font(68, bold=True)

    # Background card panel
    draw.rounded_rectangle(
        [PADDING // 2, PADDING // 2, WIDTH - PADDING // 2, HEIGHT - PADDING // 2],
        radius=16,
        fill=CARD_BG,
    )

    y = PADDING + 8

    # --- Row 1: shop name + date ---
    date_label = snapshot.report_date.strftime("%d %b %Y")
    name = (shop_name or "Your Shop").upper()
    header = f"{name}  ·  {date_label}"
    draw.text((PADDING, y), header, font=font_small, fill=MUTED_COLOR)
    y += 36

    # Divider line
    draw.line([(PADDING, y), (WIDTH - PADDING, y)], fill=(51, 65, 85), width=1)
    y += 20

    # --- Row 2: revenue (big number) ---
    revenue_text = format_price(snapshot.revenue, snapshot.currency)
    draw.text((PADDING, y), revenue_text, font=font_xlarge, fill=WHITE)
    y += 84

    # --- Row 3: delta vs yesterday ---
    delta: Decimal = comparison.get("delta", Decimal("0"))
    yesterday_revenue: Decimal = comparison.get("yesterday_revenue", Decimal("0"))

    if delta > 0:
        delta_text = f"▲  {format_price(delta, snapshot.currency)} more than yesterday"
        delta_color = ACCENT_COLOR
    elif delta < 0:
        delta_text = f"▼  {format_price(abs(delta), snapshot.currency)} less than yesterday"
        delta_color = RED_COLOR
    else:
        delta_text = "Same as yesterday"
        delta_color = MUTED_COLOR

    draw.text((PADDING, y), delta_text, font=font_medium, fill=delta_color)
    y += 40

    # --- Row 4: best day badge (conditional) ---
    if comparison.get("is_best_day_this_week"):
        badge_text = "★  Best day this week!"
        draw.text((PADDING, y), badge_text, font=font_medium, fill=GOLD)
        y += 40

    y += 8
    draw.line([(PADDING, y), (WIDTH - PADDING, y)], fill=(51, 65, 85), width=1)
    y += 16

    # --- Row 5: top sellers ---
    if snapshot.top_products:
        draw.text((PADDING, y), "Top sellers today:", font=font_small, fill=MUTED_COLOR)
        y += 26
        for name, qty in snapshot.top_products[:3]:
            line = f"  {name}  ×{qty}"
            draw.text((PADDING, y), line, font=font_medium, fill=WHITE)
            y += 32
    else:
        draw.text((PADDING, y), "No top sellers data", font=font_small, fill=MUTED_COLOR)
        y += 28

    # --- Footer ---
    footer = "Auto Tuck Shop"
    footer_x = WIDTH - PADDING - _text_width(draw, footer, font_small)
    draw.text((footer_x, HEIGHT - PADDING - 10), footer, font=font_small, fill=MUTED_COLOR)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
