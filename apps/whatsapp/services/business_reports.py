from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
import re
from typing import Iterable

from asgiref.sync import sync_to_async
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import Product
from apps.core.currencies import format_price
from apps.core.models import Company, UserProfile
from apps.sales.models import Sale, SaleItem
from apps.inventory.models import InventoryAdjustment
from apps.whatsapp.services.whatsapp_client import get_whatsapp_client


CLOSING_PROMPT_TEXT = "What time are you closing today so I can prepare today's business summary?"
CLOSING_ACK_TEXT = "Okay, noted. I will send you the summary when the shop closes."
LOW_STOCK_THRESHOLD = 5


@dataclass(frozen=True)
class BusinessSnapshot:
    company_id: int
    report_date: datetime.date
    currency: str
    sales_count: int
    items_sold: int
    revenue: Decimal
    cost: Decimal
    gross_profit: Decimal
    top_products: list[tuple[str, int]]
    low_stock_items: list[tuple[str, int]]
    out_of_stock_items: list[tuple[str, int]]


TIME_RE = re.compile(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?\b", re.IGNORECASE)


def parse_closing_time_text(text: str) -> time | None:
    """Parse a user reply like '6pm', '17:30', or 'at 8'."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return None

    match = TIME_RE.search(normalized)
    if not match:
        return None

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = match.group("meridiem")

    if meridiem:
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
    elif hour <= 7 and "close" in normalized:
        # No meridiem but a clear closing context, leave as-is.
        pass

    if hour > 23 or minute > 59:
        return None

    return time(hour=hour, minute=minute)


def _company_recipients(company: Company) -> list[str]:
    phones = list(
        UserProfile.objects.filter(company=company, user__is_active=True)
        .values_list("phone_number", flat=True)
        .distinct()
    )
    return phones or []


def _day_bounds(day: datetime.date) -> tuple[datetime, datetime]:
    start = timezone.make_aware(datetime.combine(day, time.min))
    end = timezone.make_aware(datetime.combine(day, time.max))
    return start, end


def _effective_closing_time(company: Company, day: datetime.date) -> time:
    if company.daily_closing_date == day and company.daily_closing_time:
        return company.daily_closing_time
    return company.normal_closing_time or time(17, 0)


def build_business_snapshot(company: Company, report_date: datetime.date | None = None) -> BusinessSnapshot:
    """Build a snapshot of the day's business activity."""
    report_date = report_date or timezone.localdate()
    start, end = _day_bounds(report_date)

    sales = list(
        Sale.objects.filter(
            company=company,
            status=Sale.Status.CONFIRMED,
            sale_timestamp__gte=start,
            sale_timestamp__lte=end,
        ).select_related("company").prefetch_related("items__product")
    )

    revenue = Decimal("0.00")
    cost = Decimal("0.00")
    items_sold = 0
    product_totals: dict[str, int] = {}

    for sale in sales:
        revenue += Decimal(str(sale.total_amount or 0))
        cost += sale.total_cost_amount
        for item in sale.items.all():
            items_sold += int(item.quantity or 0)
            product_totals[item.product.name] = product_totals.get(item.product.name, 0) + int(item.quantity or 0)

    top_products = sorted(product_totals.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    low_stock_items: list[tuple[str, int]] = []
    out_of_stock_items: list[tuple[str, int]] = []
    for product in Product.objects.filter(company=company, active=True):
        stock = int(product.current_stock)
        if stock <= 0:
            out_of_stock_items.append((product.name, stock))
        elif stock <= LOW_STOCK_THRESHOLD:
            low_stock_items.append((product.name, stock))

    low_stock_items.sort(key=lambda kv: (kv[1], kv[0]))
    out_of_stock_items.sort(key=lambda kv: (kv[1], kv[0]))

    gross_profit = revenue - cost

    return BusinessSnapshot(
        company_id=company.id,
        report_date=report_date,
        currency=company.currency,
        sales_count=len(sales),
        items_sold=items_sold,
        revenue=revenue,
        cost=cost,
        gross_profit=gross_profit,
        top_products=top_products,
        low_stock_items=low_stock_items,
        out_of_stock_items=out_of_stock_items,
    )


def format_business_summary(snapshot: BusinessSnapshot) -> str:
    """Render a human-readable business summary message."""
    date_label = snapshot.report_date.strftime("%d %b %Y")
    lines = [f"Business summary for {date_label}:"]
    lines.append(f"- Sales: {snapshot.sales_count}")
    lines.append(f"- Items sold: {snapshot.items_sold}")
    lines.append(f"- Revenue: {format_price(snapshot.revenue, snapshot.currency)}")
    lines.append(f"- Gross profit: {format_price(snapshot.gross_profit, snapshot.currency)}")
    lines.append("- Expenses: not tracked separately yet")

    if snapshot.top_products:
        lines.append("Top products:")
        for name, qty in snapshot.top_products[:5]:
            lines.append(f"  • {name}: {qty}")

    if snapshot.out_of_stock_items:
        lines.append("Out of stock:")
        for name, stock in snapshot.out_of_stock_items[:5]:
            lines.append(f"  • {name}: {stock}")
    elif snapshot.low_stock_items:
        lines.append("Low stock:")
        for name, stock in snapshot.low_stock_items[:5]:
            lines.append(f"  • {name}: {stock}")

    return "\n".join(lines)


def format_low_stock_summary(snapshot: BusinessSnapshot) -> str:
    lines = [f"Low stock for {snapshot.report_date.strftime('%d %b %Y')}:"]
    if not snapshot.low_stock_items and not snapshot.out_of_stock_items:
        lines.append("- No low stock items found.")
        return "\n".join(lines)

    if snapshot.out_of_stock_items:
        lines.append("Out of stock:")
        for name, stock in snapshot.out_of_stock_items[:10]:
            lines.append(f"  • {name}: {stock}")

    if snapshot.low_stock_items:
        lines.append("Low stock:")
        for name, stock in snapshot.low_stock_items[:10]:
            lines.append(f"  • {name}: {stock}")

    return "\n".join(lines)


def format_profit_summary(snapshot: BusinessSnapshot) -> str:
    lines = [f"Profit summary for {snapshot.report_date.strftime('%d %b %Y')}:"]
    lines.append(f"- Revenue: {format_price(snapshot.revenue, snapshot.currency)}")
    lines.append(f"- Product cost: {format_price(snapshot.cost, snapshot.currency)}")
    lines.append(f"- Gross profit: {format_price(snapshot.gross_profit, snapshot.currency)}")
    lines.append("- Expenses: not tracked separately yet")
    return "\n".join(lines)


@sync_to_async(thread_sensitive=True)
def set_company_daily_closing_time(company_id: int, closing_time: time, closing_date: datetime.date) -> None:
    Company.objects.filter(id=company_id).update(
        daily_closing_time=closing_time,
        daily_closing_date=closing_date,
    )


@sync_to_async(thread_sensitive=True)
def mark_closing_prompt_sent(company_id: int, prompt_date: datetime.date) -> None:
    Company.objects.filter(id=company_id).update(last_closing_prompt_date=prompt_date)


@sync_to_async(thread_sensitive=True)
def mark_summary_sent(company_id: int, summary_date: datetime.date) -> None:
    Company.objects.filter(id=company_id).update(last_summary_date=summary_date)


@sync_to_async(thread_sensitive=True)
def load_due_companies(now_date: datetime.date, now_time: time) -> list[Company]:
    return list(
        Company.objects.filter(active=True, daily_summary_enabled=True).filter(
            models.Q(last_closing_prompt_date__isnull=True) | ~models.Q(last_closing_prompt_date=now_date)
        )
    )


@sync_to_async(thread_sensitive=True)
def load_active_companies() -> list[Company]:
    return list(Company.objects.filter(active=True, daily_summary_enabled=True))


@sync_to_async(thread_sensitive=True)
def get_company_by_id(company_id: int) -> Company | None:
    return Company.objects.filter(id=company_id).first()


async def send_message_to_company(company: Company, message: str) -> list[str]:
    """Send a WhatsApp message to all known members of a company."""
    client = get_whatsapp_client()
    recipients = await sync_to_async(_company_recipients, thread_sensitive=True)(company)
    for phone in recipients:
        await client.send_message(phone, message)
    return recipients


async def send_daily_closing_prompt(company: Company) -> list[str]:
    recipients = await send_message_to_company(company, CLOSING_PROMPT_TEXT)
    await mark_closing_prompt_sent(company.id, timezone.localdate())
    return recipients


async def send_daily_summary(company: Company, report_date: datetime.date | None = None) -> list[str]:
    report_date = report_date or timezone.localdate()
    snapshot = await sync_to_async(build_business_snapshot, thread_sensitive=True)(company, report_date=report_date)
    message = format_business_summary(snapshot)
    recipients = await send_message_to_company(company, message)
    await mark_summary_sent(company.id, report_date)
    return recipients


async def send_low_stock_summary(company: Company, report_date: datetime.date | None = None) -> list[str]:
    report_date = report_date or timezone.localdate()
    snapshot = await sync_to_async(build_business_snapshot, thread_sensitive=True)(company, report_date=report_date)
    message = format_low_stock_summary(snapshot)
    return await send_message_to_company(company, message)


async def send_profit_summary(company: Company, report_date: datetime.date | None = None) -> list[str]:
    report_date = report_date or timezone.localdate()
    snapshot = await sync_to_async(build_business_snapshot, thread_sensitive=True)(company, report_date=report_date)
    message = format_profit_summary(snapshot)
    return await send_message_to_company(company, message)


async def maybe_send_daily_notifications(now: datetime | None = None) -> dict[str, list[int]]:
    """Check all active companies and send prompt or summary if due.

    Returns a dict with company IDs for actions taken.
    """
    now = now or timezone.now()
    today = timezone.localdate(now)
    current_time = now.time()
    prompt_cutoff = time(17, 0)

    companies = await load_active_companies()
    sent_prompt: list[int] = []
    sent_summary: list[int] = []

    for company in companies:
        if (
            company.last_closing_prompt_date != today
            and current_time >= prompt_cutoff
            and not (company.daily_closing_date == today and company.daily_closing_time)
        ):
            await send_daily_closing_prompt(company)
            sent_prompt.append(company.id)

        # Only send the summary once a closing time has actually been set for today.
        if company.daily_closing_date == today and company.daily_closing_time and company.last_summary_date != today:
            effective_close = _effective_closing_time(company, today)
            if current_time >= effective_close:
                await send_daily_summary(company, report_date=today)
                sent_summary.append(company.id)

    return {"prompt": sent_prompt, "summary": sent_summary}
