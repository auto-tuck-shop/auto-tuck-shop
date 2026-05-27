"""Generate a WhatsApp-ready PNG stat card for daily business reports."""

from __future__ import annotations

import datetime
import io
import logging
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from apps.whatsapp.services.business_reports import BusinessSnapshot
from apps.core.currencies import format_price

logger = logging.getLogger(__name__)

WIDTH = 1200
HEIGHT = 675

BG_COLOR = (13, 17, 28)
CARD_BG = (22, 30, 46)
ACCENT_COLOR = (74, 222, 128)
RED_COLOR = (248, 113, 113)
MUTED_COLOR = (100, 116, 139)
DIVIDER_COLOR = (38, 52, 72)
BAR_EMPTY_COLOR = (38, 52, 72)
WHITE = (255, 255, 255)
GOLD = (250, 204, 21)
BAR_COLOR = (55, 90, 160)
BAR_TODAY_COLOR = (74, 222, 128)

PADDING = 56
TOP_SELLER_MIN_QTY = 3

_FONTS_DIR = Path(__file__).parent.parent / "static" / "fonts"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    bundled = _FONTS_DIR / ("Inter-Bold.ttf" if bold else "Inter-Regular.ttf")
    if bundled.exists():
        try:
            return ImageFont.truetype(str(bundled), size)
        except Exception:
            pass
    system_candidates: list[Path] = []
    if bold:
        system_candidates = [
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ]
    else:
        system_candidates = [
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
    for path in system_candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _draw_bar_chart(
    draw: ImageDraw.ImageDraw,
    week_revenues: list[Decimal],
    report_date: datetime.date,
    x: int,
    y: int,
    w: int,
    h: int,
    font_tiny,
    font_small,
    currency: str,
) -> None:
    monday = report_date - datetime.timedelta(days=report_date.weekday())
    labels: list[str] = []
    day = monday
    for _ in week_revenues:
        labels.append("Today" if day == report_date else str(day.day))
        day += datetime.timedelta(days=1)

    n = len(week_revenues)
    if n == 0:
        return

    max_rev = max(week_revenues) if any(r > 0 for r in week_revenues) else Decimal("1")

    label_h = 28
    value_h = 24  # space above bars for value labels
    bar_area_h = h - label_h - value_h

    # Fixed equal bar widths with consistent gaps
    gap = 16
    bar_w = (w - gap * (n - 1)) // n

    for i, rev in enumerate(week_revenues):
        bx = x + i * (bar_w + gap)
        is_today = (i == n - 1)

        # Empty bar outline for zero-revenue days
        if rev <= 0:
            draw.rounded_rectangle(
                [bx, y + value_h, bx + bar_w, y + value_h + bar_area_h],
                radius=4,
                outline=BAR_EMPTY_COLOR,
                width=2,
            )
        else:
            bar_h = max(6, int(bar_area_h * float(rev) / float(max_rev)))
            by = y + value_h + bar_area_h - bar_h
            color = BAR_TODAY_COLOR if is_today else BAR_COLOR
            draw.rounded_rectangle([bx, by, bx + bar_w, y + value_h + bar_area_h], radius=4, fill=color)

            # Value label above each bar
            val_text = format_price(rev, currency)
            vw = _tw(draw, val_text, font_tiny)
            val_color = ACCENT_COLOR if is_today else MUTED_COLOR
            draw.text(
                (bx + (bar_w - vw) // 2, by - 26),
                val_text, font=font_tiny, fill=val_color,
            )

        # Day label below bar
        lw = _tw(draw, labels[i], font_tiny)
        label_color = WHITE if is_today else MUTED_COLOR
        draw.text(
            (bx + (bar_w - lw) // 2, y + value_h + bar_area_h + 8),
            labels[i], font=font_tiny, fill=label_color,
        )


def generate_stat_card(snapshot: BusinessSnapshot, comparison: dict, shop_name: str = "") -> bytes:
    img = Image.new("RGB", (WIDTH, HEIGHT), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_tiny = _load_font(18)
    font_small = _load_font(22)
    font_medium = _load_font(28)
    font_hero = _load_font(80, bold=True)

    draw.rounded_rectangle(
        [PADDING // 2, PADDING // 2, WIDTH - PADDING // 2, HEIGHT - PADDING // 2],
        radius=20,
        fill=CARD_BG,
    )

    y = PADDING + 12

    # Header
    date_label = snapshot.report_date.strftime("%d %b %Y")
    display_name = (shop_name or "Your Shop").upper()
    header = f"{display_name}  |  {date_label}"
    draw.text((PADDING, y), header, font=font_small, fill=MUTED_COLOR)
    y += 38
    draw.line([(PADDING, y), (WIDTH - PADDING, y)], fill=DIVIDER_COLOR, width=1)
    y += 24

    content_top = y  # remember where content starts for chart alignment

    # Left / right column split
    col_gap = 48
    left_w = WIDTH // 2 - PADDING - col_gap // 2
    right_x = WIDTH // 2 + col_gap // 2
    right_w = WIDTH - right_x - PADDING

    # Revenue hero
    revenue_text = format_price(snapshot.revenue, snapshot.currency)
    draw.text((PADDING, y), revenue_text, font=font_hero, fill=WHITE)
    y += font_hero.size + 16

    # Sales count
    sales_label = f"{snapshot.sales_count} sale{'s' if snapshot.sales_count != 1 else ''} today"
    draw.text((PADDING, y), sales_label, font=font_small, fill=MUTED_COLOR)
    y += 36

    # Delta vs yesterday — only meaningful if yesterday had sales
    delta: Decimal = comparison.get("delta", Decimal("0"))
    yesterday_revenue: Decimal = comparison.get("yesterday_revenue", Decimal("0"))
    if yesterday_revenue > 0:
        if delta > 0:
            delta_text = f"+ {format_price(delta, snapshot.currency)} vs yesterday"
            delta_color = ACCENT_COLOR
        elif delta < 0:
            delta_text = f"- {format_price(abs(delta), snapshot.currency)} vs yesterday"
            delta_color = RED_COLOR
        else:
            delta_text = "Same as yesterday"
            delta_color = MUTED_COLOR
        draw.text((PADDING, y), delta_text, font=font_medium, fill=delta_color)
        y += 44

    # Best day badge — only meaningful if there are prior days this week to compare
    week_revenues: list[Decimal] = comparison.get("week_revenues", [])
    prior_days_had_sales = any(r > 0 for r in week_revenues[:-1])
    if comparison.get("is_best_day_this_week") and prior_days_had_sales:
        draw.text((PADDING, y), "Best day this week", font=font_medium, fill=GOLD)
        y += 40

    y += 8
    draw.line([(PADDING, y), (PADDING + left_w, y)], fill=DIVIDER_COLOR, width=1)
    y += 18

    # Top sellers — only items with qty >= TOP_SELLER_MIN_QTY
    top = [(name, qty) for name, qty in snapshot.top_products if qty >= TOP_SELLER_MIN_QTY]
    if top:
        draw.text((PADDING, y), "Top sellers", font=font_small, fill=MUTED_COLOR)
        y += 28
        for pname, qty in top[:3]:
            draw.text((PADDING, y), pname, font=font_medium, fill=WHITE)
            qty_text = f"x{qty}"
            qty_x = PADDING + left_w - _tw(draw, qty_text, font_medium)
            draw.text((qty_x, y), qty_text, font=font_medium, fill=MUTED_COLOR)
            y += 36

    # Right column: bar chart
    if week_revenues:
        chart_label = "This week"
        draw.text((right_x, content_top), chart_label, font=font_small, fill=MUTED_COLOR)
        chart_top = content_top + 32
        chart_h = HEIGHT - chart_top - PADDING - 8
        _draw_bar_chart(
            draw, week_revenues, snapshot.report_date,
            right_x, chart_top, right_w, chart_h,
            font_tiny, font_small, snapshot.currency,
        )

    # Footer
    footer = "Auto Tuck Shop"
    fw = _tw(draw, footer, font_tiny)
    draw.text((WIDTH - PADDING - fw, HEIGHT - PADDING + 4), footer, font=font_tiny, fill=MUTED_COLOR)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
