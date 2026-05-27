"""Sale processing and sale button actions."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from django.db import close_old_connections

from apps.core.currencies import format_price
from apps.core.models import Company
from apps.sales.models import Sale
from apps.sales.services import create_sale_from_parsed_items, MissingPriceError, PriceOverflowError
from apps.whatsapp.services.webhook_handler import (
    db_sync_to_async,
    DEFAULT_LANGUAGE,
    _extract_phone_number,
    _get_profile_by_phone,
    _send_response,
    _send_response_with_buttons,
    run_async,
    t,
)
from utils.timing import track

logger = logging.getLogger(__name__)


async def _create_sale(parsed_items, message_id, company, currency=None):
    async with track("create_sale"):
        @db_sync_to_async
        def _create_sale_sync():
            return create_sale_from_parsed_items(
                items=parsed_items,
                whatsapp_message_id=message_id,
                company=company,
                currency=currency,
            )
        return await _create_sale_sync()


@db_sync_to_async
def _get_sale_items(sale):
    return list(sale.items.select_related("product").all())


@db_sync_to_async
def _store_response_message_sid(sale_id: int, message_sid: str) -> None:
    Sale.objects.filter(id=sale_id).update(confirmation_message_sid=message_sid)


@db_sync_to_async
def _update_company_currency(company_id: int, currency: str) -> None:
    Company.objects.filter(id=company_id).update(currency=currency)


@db_sync_to_async
def _get_confirmed_sale(original_message_sid: str | None):
    if not original_message_sid:
        return None
    try:
        sale = Sale.objects.get(
            confirmation_message_sid=original_message_sid,
            status=Sale.Status.CONFIRMED,
        )
        return sale, sale.whatsapp_message_id
    except Sale.DoesNotExist:
        return None


@db_sync_to_async
def _is_first_confirmed_sale(company_id: int) -> bool:
    return Sale.objects.filter(
        company_id=company_id,
        status=Sale.Status.CONFIRMED,
    ).count() == 1


@db_sync_to_async
def _company_needs_closing_time(company_id: int) -> bool:
    company = Company.objects.filter(id=company_id).only("normal_closing_time").first()
    return company is not None and company.normal_closing_time is None


@db_sync_to_async
def _get_and_update_sale(original_message_sid: str | None, new_status: str, bot_mistake: bool = False):
    if not original_message_sid:
        return None
    try:
        sale = Sale.objects.get(
            confirmation_message_sid=original_message_sid,
            status=Sale.Status.CONFIRMED,
        )
        sale.status = new_status
        if bot_mistake:
            sale.flagged_as_bot_mistake = True
        sale.save(update_fields=["status", "flagged_as_bot_mistake"])
        return sale, sale.whatsapp_message_id
    except Sale.DoesNotExist:
        return None


async def process_sale_message_unified(
    message_id: str,
    sender: str,
    text: str,
    company,
    result,
    is_from_audio: bool = False,
    lang: str = DEFAULT_LANGUAGE,
) -> None:
    if not result.items:
        await _send_response(sender, t("sale.no_products", lang=lang))
        return

    if result.currency and company:
        await _update_company_currency(company.id, result.currency)
        company.currency = result.currency

    try:
        sale_result = await _create_sale(result.items, message_id, company, currency=result.currency)
    except PriceOverflowError:
        logger.warning("Price overflow for message %s from %s", message_id, sender)
        await _send_response(sender, t("sale.price_too_large", lang=lang), reply_to=message_id)
        return
    except MissingPriceError as e:
        logger.info("Missing price for message %s from %s: %s", message_id, sender, e)
        await _send_response(sender, t("sale.missing_price_reject", lang=lang), reply_to=message_id)
        return

    sale = sale_result["sale"]
    unmatched = sale_result["unmatched_items"]

    response_lines = []
    sale_items = await _get_sale_items(sale)
    currency_totals: dict[str, Decimal] = {}

    for item in sale_items:
        currency_totals[item.currency] = (
            currency_totals.get(item.currency, Decimal("0"))
            + item.unit_price * item.quantity
        )
        response_lines.append(t(
            "sale.item_with_price", lang=lang,
            quantity=item.quantity, product=item.product.name,
            price=format_price(item.unit_price, item.currency),
        ))

    if len(currency_totals) == 1:
        currency, total = next(iter(currency_totals.items()))
        response_lines.append(t("sale.total", lang=lang, total=format_price(total, currency)))
    else:
        for currency, total in currency_totals.items():
            response_lines.append(t("sale.subtotal", lang=lang, currency=currency, total=format_price(total, currency)))

    if unmatched:
        response_lines.append(t("sale.unmatched", lang=lang, items=", ".join(unmatched)))

    buttons = [
        {"id": f"confirm_{sale.id}", "title": t("sale.btn_confirm", lang=lang)},
        {"id": f"fix_{sale.id}", "title": t("sale.btn_fix", lang=lang)},
    ]
    message_sid = await _send_response_with_buttons(
        sender, "\n".join(response_lines), buttons, reply_to=message_id
    )
    if message_sid:
        await _store_response_message_sid(sale.id, message_sid)


async def process_sale_button_action_async(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    if action == "confirm":
        result = await _get_confirmed_sale(original_message_sid)
        if result:
            sale, original_whatsapp_message_id = result
            await _send_response(sender, t("sale.confirmed_ok", lang=lang), reply_to=original_whatsapp_message_id)
            # After first confirmed sale, ask for closing time if not yet set
            if await _company_needs_closing_time(sale.company_id) and await _is_first_confirmed_sale(sale.company_id):
                await asyncio.sleep(3)
                await _send_response(sender, t("closing.setup_prompt", lang=lang))
        else:
            await _send_response(sender, t("sale.already_processed", lang=lang))
    else:
        result = await _get_and_update_sale(original_message_sid, Sale.Status.CANCELLED, bot_mistake=True)
        if result:
            sale, original_whatsapp_message_id = result
            await _send_response(sender, t("sale.bot_mistake", lang=lang), reply_to=original_whatsapp_message_id)
        else:
            await _send_response(sender, t("sale.already_processed", lang=lang))


def handle_sale_button_action(action: str, sender: str, original_message_sid: str | None = None) -> None:
    try:
        close_old_connections()
        run_async(process_sale_button_action_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling sale {action}: {e}")
