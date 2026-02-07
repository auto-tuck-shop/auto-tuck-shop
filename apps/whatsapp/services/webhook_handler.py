"""Service for handling incoming WhatsApp messages."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.db import close_old_connections
from django.utils import timezone
from django.utils.text import slugify

from apps.core.currencies import format_price
from apps.core.models import Company, UserProfile, WaitlistEntry
from apps.sales.models import Sale
from apps.sales.services import create_sale_from_parsed_items
from apps.whatsapp.services.message_parser import parse_message_unified
from apps.whatsapp.services.whatsapp_client import WhatsAppClient
from services.openrouter.client import OpenRouterError
from utils.timing import start_tracking, end_tracking, track

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Load localization strings
_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_locale_cache = {
    "en": json.loads((_LOCALES_DIR / "en.json").read_text(encoding="utf-8")),
    "sn": json.loads((_LOCALES_DIR / "sn.json").read_text(encoding="utf-8")),
}


def t(key: str, language: str = "en", **kwargs) -> str:
    """Get localized string by dot-notation key, with optional format args."""
    strings = _locale_cache.get(language, _locale_cache["en"])
    keys = key.split(".")
    value = strings
    for k in keys:
        value = value[k]
    return value.format(**kwargs) if kwargs else value


# Admin phone number for waitlist approval notifications
ADMIN_PHONE_NUMBER = "+14342183470"


def _extract_phone_number(sender: str) -> str:
    """
    Extract and normalize phone number.

    Meta sends plain numbers like '1234567890'.
    Stored numbers have '+' prefix like '+1234567890'.
    """
    # Remove whatsapp: prefix if present (backwards compatibility)
    if sender.startswith("whatsapp:"):
        sender = sender[9:]
    # Ensure + prefix for database storage
    if not sender.startswith("+"):
        sender = f"+{sender}"
    return sender


@sync_to_async
def _upload_to_r2(media_data: bytes, media_id: str, mime_type: str, phone_number: str) -> str | None:
    """
    Upload media file to R2 storage.

    Args:
        media_data: The raw media file bytes
        media_id: The Meta media ID
        mime_type: The MIME type of the media
        phone_number: The phone number of the sender

    Returns:
        The public URL of the uploaded file, or None if upload failed
    """
    try:
        from services.storage import R2StorageClient
        client = R2StorageClient()
        return client.upload_media(media_data, media_id, mime_type, phone_number)
    except Exception as e:
        logger.error(f"Failed to upload media to R2: {e}", exc_info=True)
        return None


def handle_new_waitlist_entry(sender: str, text: str) -> None:
    """
    Handle a message from an unknown phone number by adding them to the waitlist.

    Args:
        sender: The sender's phone number (e.g., whatsapp:+1234567890)
        text: The message text
    """
    try:
        close_old_connections()
        asyncio.run(_process_new_waitlist_entry_async(sender, text))
    except Exception as e:
        logger.exception(f"Error handling new waitlist entry for {sender}: {e}")


def handle_waitlisted_message(sender: str, text: str, waitlist_entry: WaitlistEntry) -> None:
    """
    Handle a message from a user who is already on the waitlist.

    Args:
        sender: The sender's phone number
        text: The message text
        waitlist_entry: The existing waitlist entry
    """
    try:
        close_old_connections()
        asyncio.run(_process_waitlisted_message_async(sender, text, waitlist_entry))
    except Exception as e:
        logger.exception(f"Error handling waitlisted message for {sender}: {e}")


def handle_incoming_message(
    message_id: str,
    sender: str,
    text: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Handle an incoming WhatsApp message from a known user.

    This function runs the async processing in a new event loop.
    In production, you might want to use Celery or similar for background processing.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        text: The message text
        user_profile: The user profile of the sender
    """
    try:
        # Close old database connections before creating new event loop
        close_old_connections()
        asyncio.run(_process_message_async(message_id, sender, text, user_profile))
    except Exception as e:
        logger.exception(f"Error handling message {message_id}: {e}")


def handle_incoming_audio_message(
    message_id: str,
    sender: str,
    media_id: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Handle an incoming WhatsApp audio message from a known user.

    Downloads audio, transcribes it, and processes the transcription.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        media_id: The Meta media ID
        user_profile: The user profile of the sender
    """
    try:
        # Close old database connections before creating new event loop
        close_old_connections()
        asyncio.run(_process_audio_message_async(message_id, sender, media_id, user_profile))
    except Exception as e:
        logger.exception(f"Error handling audio message {message_id}: {e}")


async def _process_new_waitlist_entry_async(sender: str, text: str) -> None:
    """Add a new user to the waitlist and send a welcome message."""
    phone_number = _extract_phone_number(sender)

    # Create waitlist entry
    entry = await _create_waitlist_entry(phone_number, text)

    # Send welcome message (default to English for waitlist)
    await _send_response(sender, t("waitlist.welcome", language="en"))

    # Send admin notification with approve/reject buttons
    await _send_waitlist_admin_notification(entry)


async def _process_waitlisted_message_async(sender: str, text: str, waitlist_entry: WaitlistEntry) -> None:
    """Handle a message from a waitlisted user."""
    if waitlist_entry.status == WaitlistEntry.Status.PENDING:
        # If they don't have a company name yet, save this message as their company name
        if not waitlist_entry.company_name and text.strip():
            await _update_waitlist_company_name(waitlist_entry.id, text.strip())
            await _send_response(
                sender,
                t("waitlist.shop_name_noted", language="en", shop_name=text.strip()),
            )
        else:
            await _send_response(sender, t("waitlist.still_pending", language="en"))
    elif waitlist_entry.status == WaitlistEntry.Status.REJECTED:
        await _send_response(sender, t("waitlist.rejected", language="en"))


@sync_to_async
def _update_waitlist_company_name(entry_id: int, company_name: str) -> None:
    """Update the company name on a waitlist entry."""
    WaitlistEntry.objects.filter(id=entry_id).update(company_name=company_name)


@sync_to_async
def _create_waitlist_entry(phone_number: str, first_message: str) -> WaitlistEntry:
    """Create a new waitlist entry."""
    entry, created = WaitlistEntry.objects.get_or_create(
        phone_number=phone_number,
        defaults={"first_message": first_message},
    )
    if not created and not entry.first_message:
        entry.first_message = first_message
        entry.save(update_fields=["first_message"])
    return entry


async def _send_waitlist_admin_notification(entry: WaitlistEntry) -> None:
    """Send a notification to the admin with approve/reject buttons."""
    message = t(
        "waitlist_admin.new_request",
        language="en",
        phone=entry.phone_number,
        message=entry.first_message[:100] if entry.first_message else "(none)",
    )

    buttons = [
        {"id": f"waitlist_approve_{entry.id}", "title": t("waitlist_admin.btn_approve", language="en")},
        {"id": f"waitlist_reject_{entry.id}", "title": t("waitlist_admin.btn_reject", language="en")},
    ]

    message_sid = await _send_response_with_buttons(
        ADMIN_PHONE_NUMBER,
        message,
        buttons,
    )

    # Store the confirmation message SID for lookup when button is clicked
    if message_sid:
        await _store_waitlist_confirmation_sid(entry.id, message_sid)


@sync_to_async
def _store_waitlist_confirmation_sid(entry_id: int, message_sid: str) -> None:
    """Store the confirmation message SID on the waitlist entry."""
    WaitlistEntry.objects.filter(id=entry_id).update(confirmation_message_sid=message_sid)


async def _create_sale(parsed_items, message_id, company, currency=None):
    """Create sale from parsed items (async wrapper with timing)."""
    async with track("create_sale"):
        @sync_to_async
        def _create_sale_sync():
            return create_sale_from_parsed_items(
                items=parsed_items,
                whatsapp_message_id=message_id,
                company=company,
                currency=currency,
            )
        return await _create_sale_sync()


@sync_to_async
def _get_sale_items(sale):
    """Get sale items for response (sync wrapper for async context)."""
    items = list(sale.items.select_related("product").all())
    return items


async def _process_message_async(
    message_id: str,
    sender: str,
    text: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Async processing of the incoming message.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        text: The message text
        user_profile: The user profile of the sender
    """
    start_tracking(request_id=message_id)
    try:
        # Check if user is in onboarding
        if user_profile and user_profile.onboarding_step != UserProfile.OnboardingStep.COMPLETE:
            await _handle_onboarding_message(sender, text, user_profile)
            return

        company = user_profile.company if user_profile else None
        language = user_profile.language if user_profile else "en"

        # Check for summary preference response (1=Yes, 2=No) - simple heuristic
        text_lower = text.lower().strip()
        if text_lower in ["1", "yes", "yeah", "yep", "sure"]:
            # They might be saying yes to daily summaries
            # Check if their last summary was recent (within last 5 minutes = likely responding to prompt)
            from django.utils import timezone
            if user_profile.last_summary_date == timezone.now().date():
                await _update_user_profile(user_profile.id, daily_summary_enabled=True)
                await _send_response(sender, t("end_of_day.summary_enabled", language=language))
                return
        elif text_lower in ["2", "no", "nope", "nah", "skip"]:
            from django.utils import timezone
            if user_profile.last_summary_date == timezone.now().date():
                await _update_user_profile(user_profile.id, daily_summary_enabled=False)
                await _send_response(sender, t("end_of_day.summary_disabled", language=language))
                return

        # Check for summary request (not "closing" - just an on-demand report)
        if text_lower in ["done for today", "closing", "close day", "end of day", "done", "summary", "report"]:
            await _send_daily_summary(sender, user_profile, requested=True)
            return
        
        # Check for weekly summary request
        if text_lower in ["weekly summary", "week", "weekly", "this week", "weekly report"]:
            await _send_weekly_summary(sender, user_profile)
            return
        
        # Check for monthly summary request
        if text_lower in ["monthly summary", "month", "monthly", "this month", "monthly report"]:
            await _send_monthly_summary(sender, user_profile)
            return
        
        # Help with summary commands
        if text_lower in ["help", "summary help", "summaries"]:
            await _send_response(sender, t("summary_help.message", language=language))
            return

        # Check if this is a new day - send auto-summary for yesterday if needed
        await _check_and_send_day_change_summary(sender, user_profile)

        # UNIFIED: Single LLM call for intent + extraction
        try:
            result = await parse_message_unified(text)
        except OpenRouterError as e:
            logger.error(f"LLM processing failed for message {message_id}: {e}")
            await _send_response(sender, t("error.processing_failed", language=language))
            return

        logger.info(f"Parsed message - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, text, user_profile, result)
            return

        # Default: treat as sale
        await _process_sale_message_unified(message_id, sender, text, company, result, language=language)
    finally:
        end_tracking()


async def _process_audio_message_async(
    message_id: str,
    sender: str,
    media_id: str,
    user_profile: UserProfile | None = None,
) -> None:
    """
    Async processing of incoming audio message.

    Downloads audio, transcribes it, and processes the transcription.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        media_id: The Meta media ID
        user_profile: The user profile of the sender
    """
    start_tracking(request_id=message_id)
    try:
        # Check if user is in onboarding
        if user_profile and user_profile.onboarding_step != UserProfile.OnboardingStep.COMPLETE:
            await _handle_onboarding_message(sender, "[Audio message]", user_profile)
            return

        company = user_profile.company if user_profile else None
        phone_number = _extract_phone_number(sender)
        language = user_profile.language if user_profile else "en"

        # Step 1: Download audio from Meta CDN (we need the bytes for both transcription and R2)
        whatsapp_client = WhatsAppClient()
        media_result = await whatsapp_client.download_media(media_id)

        if not media_result:
            await _send_response(sender, t("audio.download_failed", language=language))
            return

        audio_data, mime_type = media_result

        # Step 2: Run transcription and R2 upload IN PARALLEL (both use the same audio_data)
        from services.elevenlabs import ElevenLabsClient, ElevenLabsError

        # Map MIME type to file extension
        mime_to_extension = {
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/aac": "aac",
            "audio/amr": "amr",
        }
        extension = mime_to_extension.get(mime_type, "ogg")
        filename = f"audio.{extension}"

        try:
            elevenlabs_client = ElevenLabsClient()

            # Fire-and-forget R2 upload (archival, not on critical path)
            async def _background_r2_upload():
                """Upload to R2 and update DB in background."""
                r2_url = await _upload_to_r2(audio_data, media_id, mime_type, phone_number)
                if r2_url:
                    @sync_to_async
                    def _update_r2_url(msg_id: str, url: str):
                        from apps.whatsapp.models import WhatsAppMessage
                        WhatsAppMessage.objects.filter(whatsapp_message_id=msg_id).update(r2_media_url=url)

                    await _update_r2_url(message_id, r2_url)
                    logger.info(f"R2 upload completed for {message_id}: {r2_url}")

            # Start R2 upload in background (don't await)
            asyncio.create_task(_background_r2_upload())

            # Only await transcription (on critical path)
            transcribed_text = await elevenlabs_client.transcribe_audio(audio_data, filename)

            logger.info(f"Transcribed audio message {message_id}: {transcribed_text[:100]}...")

            # Step 3: Update database with transcription (R2 URL updated separately in background)
            @sync_to_async
            def _update_message_transcription(msg_id: str, transcript: str):
                from apps.whatsapp.models import WhatsAppMessage
                WhatsAppMessage.objects.filter(whatsapp_message_id=msg_id).update(
                    transcribed_text=transcript,
                    content=transcript,
                )

            await _update_message_transcription(message_id, transcribed_text)

        except ElevenLabsError as e:
            logger.error(f"Failed to transcribe audio message {message_id}: {e}")
            await _send_response(sender, t("audio.transcription_failed", language=language))
            return

        # Step 4: Unified parse (intent + extraction)
        try:
            result = await parse_message_unified(transcribed_text)
        except OpenRouterError as e:
            logger.error(f"LLM processing failed for audio message {message_id}: {e}")
            await _send_response(sender, t("error.processing_failed", language=language))
            return

        logger.info(f"Parsed audio - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, transcribed_text, user_profile, result)
            return

        # Default: treat as sale
        await _process_sale_message_unified(
            message_id, sender, transcribed_text, company, result, is_from_audio=True, language=language
        )
    finally:
        end_tracking()


async def _handle_onboarding_message(sender: str, text: str, user_profile: UserProfile) -> None:
    """Handle messages during onboarding flow."""
    step = user_profile.onboarding_step
    language = user_profile.language
    text_clean = text.strip().lower()

    if step == UserProfile.OnboardingStep.LANGUAGE:
        # Language selection
        if text_clean in ["1", "english", "en"]:
            await _update_user_profile(user_profile.id, language="en", onboarding_step=UserProfile.OnboardingStep.ROLE)
            await _send_response(sender, t("approval.welcome", language="en", company=user_profile.company.name))
            await _send_response(sender, t("onboarding.role_selection", language="en"))
        elif text_clean in ["2", "shona", "sn"]:
            await _update_user_profile(user_profile.id, language="sn", onboarding_step=UserProfile.OnboardingStep.ROLE)
            await _send_response(sender, t("approval.welcome", language="sn", company=user_profile.company.name))
            await _send_response(sender, t("onboarding.role_selection", language="sn"))
        else:
            # Re-ask
            await _send_response(sender, t("onboarding.language_selection", language="en"))

    elif step == UserProfile.OnboardingStep.ROLE:
        # Role selection
        role = None
        if text_clean in ["1", "owner", "shop owner"]:
            role = UserProfile.Role.OWNER
        elif text_clean in ["2", "assistant", "shop assistant"]:
            role = UserProfile.Role.ASSISTANT
        elif text_clean in ["3", "both"]:
            role = UserProfile.Role.BOTH

        if role:
            await _update_user_profile(user_profile.id, role=role, onboarding_step=UserProfile.OnboardingStep.ASSISTANT_LINK)
            # Only ask about adding assistant if they're an owner or both
            if role in [UserProfile.Role.OWNER, UserProfile.Role.BOTH]:
                await _send_response(sender, t("onboarding.assistant_linking_ask", language=language))
            else:
                # Skip to value explanation
                await _update_user_profile(user_profile.id, onboarding_step=UserProfile.OnboardingStep.STOCK_SETUP)
                await _send_response(sender, t("onboarding.value_explanation", language=language))
                await _send_response(sender, t("onboarding.stock_setup_ask", language=language))
        else:
            await _send_response(sender, t("onboarding.role_selection", language=language))

    elif step == UserProfile.OnboardingStep.ASSISTANT_LINK:
        # Assistant linking
        if text_clean in ["1", "add another number", "add", "yes"]:
            await _send_response(sender, t("onboarding.assistant_linking_prompt", language=language))
            # Wait for next message with phone number (we'll handle it in the same step)
        elif text_clean in ["2", "skip for now", "skip", "no"]:
            await _update_user_profile(user_profile.id, onboarding_step=UserProfile.OnboardingStep.STOCK_SETUP)
            await _send_response(sender, t("onboarding.assistant_linking_skipped", language=language))
            await _send_response(sender, t("onboarding.value_explanation", language=language))
            await _send_response(sender, t("onboarding.stock_setup_ask", language=language))
        elif text_clean.startswith("+") or text_clean.startswith("0") or text_clean.isdigit():
            # Looks like a phone number - add assistant
            result = await parse_message_unified(f"add assistant {text}")
            if result.phone_number:
                existing_profile = await _get_profile_by_phone(result.phone_number)
                if not existing_profile:
                    await _create_assistant(result.phone_number, user_profile.company)
                    await _send_response(sender, t("assistant.added", language=language, phone=result.phone_number, company=user_profile.company.name))
                else:
                    await _send_response(sender, t("assistant.already_registered", language=language, phone=result.phone_number))
            # Move to next step
            await _update_user_profile(user_profile.id, onboarding_step=UserProfile.OnboardingStep.STOCK_SETUP)
            await _send_response(sender, t("onboarding.value_explanation", language=language))
            await _send_response(sender, t("onboarding.stock_setup_ask", language=language))
        else:
            await _send_response(sender, t("onboarding.assistant_linking_ask", language=language))

    elif step == UserProfile.OnboardingStep.STOCK_SETUP:
        # Stock setup decision
        if text_clean in ["1", "add stock now", "add", "yes"]:
            await _update_user_profile(user_profile.id, onboarding_step=UserProfile.OnboardingStep.STOCK_ADDING)
            await _send_response(sender, t("onboarding.stock_setup_prompt", language=language))
        elif text_clean in ["2", "skip for now", "skip", "no"]:
            await _update_user_profile(user_profile.id, onboarding_step=UserProfile.OnboardingStep.COMPLETE)
            await _send_response(sender, t("onboarding.stock_setup_skipped", language=language))
            await _send_response(sender, t("onboarding.sales_instruction", language=language))
            await _send_response(sender, t("onboarding.onboarding_complete", language=language))
        else:
            await _send_response(sender, t("onboarding.stock_setup_ask", language=language))

    elif step == UserProfile.OnboardingStep.STOCK_ADDING:
        # Adding stock items
        if text_clean in ["done", "done adding stock", "finish", "complete"]:
            await _update_user_profile(user_profile.id, onboarding_step=UserProfile.OnboardingStep.COMPLETE)
            await _send_response(sender, t("onboarding.stock_setup_complete", language=language))
            await _send_response(sender, t("onboarding.sales_instruction", language=language))
            await _send_response(sender, t("onboarding.onboarding_complete", language=language))
        else:
            # Parse the stock items as a sale to create products
            try:
                result = await parse_message_unified(text)
                if result.items:
                    # Create products from the parsed items
                    await _create_products_from_items(result.items, user_profile.company, result.currency)
                    await _send_response(sender, t("sale.confirmed", language=language))
                    await _send_response(sender, t("onboarding.stock_setup_prompt", language=language))
                else:
                    await _send_response(sender, t("sale.no_products", language=language))
            except OpenRouterError as e:
                logger.error(f"Failed to parse stock items: {e}")
                await _send_response(sender, t("error.processing_failed", language=language))


async def _check_and_send_day_change_summary(sender: str, user_profile: UserProfile) -> None:
    """
    Check if this is a new day. If so, auto-send yesterday's summary if user never requested it.
    This runs before processing the first sale of a new day.
    """
    from django.utils import timezone
    from datetime import timedelta
    
    today = timezone.now().date()
    last_summary_date = user_profile.last_summary_date
    
    # If they've never received a summary, skip
    if not last_summary_date:
        return
    
    # If last summary was today or yesterday, no need to send
    if last_summary_date >= today - timedelta(days=1):
        return
    
    # They haven't received a summary since before yesterday - send yesterday's
    language = user_profile.language
    yesterday = today - timedelta(days=1)
    
    @sync_to_async
    def _has_sales_for_date(date):
        from apps.sales.models import Sale
        return Sale.objects.filter(
            company=user_profile.company,
            created_at__date=date,
            status=Sale.Status.CONFIRMED
        ).exists()
    
    # Only send if they had sales yesterday
    if await _has_sales_for_date(yesterday):
        summary_details = await _get_daily_summary(user_profile.company, yesterday)
        await _send_response(
            sender,
            f"📊 You didn't get yesterday's summary. Here it is:\n\n{summary_details}"
        )
        await _update_user_profile(user_profile.id, last_summary_date=today)


async def _send_daily_summary(sender: str, user_profile: UserProfile, requested: bool = False) -> None:
    """
    Send daily summary for today. This does NOT close the day - user can continue logging sales.
    
    Args:
        sender: Phone number
        user_profile: User profile
        requested: True if user explicitly requested it, False if auto-sent
    """
    from django.utils import timezone
    
    language = user_profile.language
    company = user_profile.company
    today = timezone.now().date()
    
    summary_details = await _get_daily_summary(company, today)
    
    # Update last summary date
    await _update_user_profile(user_profile.id, last_summary_date=today)
    
    if requested:
        # User asked for it - include the preference question
        await _send_response(
            sender,
            t("end_of_day.summary", language=language, summary_details=summary_details)
        )
    else:
        # Auto-sent - just send the summary without asking
        await _send_response(sender, f"📊 Daily Summary:\n\n{summary_details}")


@sync_to_async
def _get_daily_summary(company, date) -> str:
    """Get formatted daily summary for a specific date."""
    from apps.sales.models import Sale
    from decimal import Decimal
    from collections import defaultdict
    
    sales = Sale.objects.filter(
        company=company,
        created_at__date=date,
        status=Sale.Status.CONFIRMED
    ).select_related().prefetch_related('items')

    total_by_currency = defaultdict(Decimal)
    sale_count = 0
    product_counts = defaultdict(int)

    for sale in sales:
        sale_count += 1
        for item in sale.items.all():
            if item.unit_price and item.currency:
                total_by_currency[item.currency] += item.unit_price * item.quantity
            if item.product:
                product_counts[item.product.name] += item.quantity

    best_selling = max(product_counts.items(), key=lambda x: x[1])[0] if product_counts else "N/A"

    # Format summary
    currency_lines = []
    for currency, total in total_by_currency.items():
        currency_lines.append(f"{format_price(total, currency)}")

    summary = f"Total sales:\n" + "\n".join(currency_lines) if currency_lines else "No sales recorded"
    summary += f"\n\nNumber of sales recorded: {sale_count}"
    summary += f"\nBest-selling item: {best_selling}"

    return summary


async def _handle_end_of_day(sender: str, user_profile: UserProfile) -> None:
    """
    DEPRECATED: Use _send_daily_summary instead.
    Keeping for backwards compatibility during transition.
    """
    await _send_daily_summary(sender, user_profile, requested=True)


async def _send_weekly_summary(sender: str, user_profile: UserProfile) -> None:
    """Send weekly summary (Monday to Sunday of current week)."""
    from django.utils import timezone
    from datetime import timedelta
    
    language = user_profile.language
    company = user_profile.company
    
    # Get current week (Monday to Sunday)
    today = timezone.now().date()
    weekday = today.weekday()  # Monday=0, Sunday=6
    week_start = today - timedelta(days=weekday)  # This Monday
    week_end = week_start + timedelta(days=6)  # This Sunday
    
    @sync_to_async
    def _get_week_summary():
        from apps.sales.models import Sale
        from decimal import Decimal
        from collections import defaultdict
        
        sales = Sale.objects.filter(
            company=company,
            created_at__date__gte=week_start,
            created_at__date__lte=week_end,
            status=Sale.Status.CONFIRMED
        ).select_related().prefetch_related('items')
        
        if not sales.exists():
            return t("weekly_summary.no_sales", language=language)
        
        total_by_currency = defaultdict(Decimal)
        sale_count = 0
        product_counts = defaultdict(int)
        daily_totals = defaultdict(lambda: defaultdict(Decimal))
        
        for sale in sales:
            sale_count += 1
            sale_date = sale.created_at.date()
            for item in sale.items.all():
                if item.unit_price and item.currency:
                    amount = item.unit_price * item.quantity
                    total_by_currency[item.currency] += amount
                    daily_totals[sale_date][item.currency] += amount
                if item.product:
                    product_counts[item.product.name] += item.quantity
        
        best_selling = max(product_counts.items(), key=lambda x: x[1])[0] if product_counts else "N/A"
        
        # Format summary
        currency_lines = []
        for currency, total in total_by_currency.items():
            currency_lines.append(f"{format_price(total, currency)}")
            
        summary = "Total sales:\n" + "\n".join(currency_lines)
        summary += f"\n\nNumber of sales: {sale_count}"
        summary += f"\nBest-selling item: {best_selling}"
        
        # Add daily breakdown
        summary += "\n\n📅 Daily breakdown:"
        for day_offset in range(7):
            day_date = week_start + timedelta(days=day_offset)
            if day_date in daily_totals:
                day_name = day_date.strftime("%a")  # Mon, Tue, etc
                day_currencies = []
                for currency, amount in daily_totals[day_date].items():
                    day_currencies.append(format_price(amount, currency))
                summary += f"\n{day_name} {day_date.strftime('%m/%d')}: {', '.join(day_currencies)}"
        
        return summary
    
    summary_details = await _get_week_summary()
    
    title = t(
        "weekly_summary.title",
        language=language,
        start_date=week_start.strftime("%b %d"),
        end_date=week_end.strftime("%b %d")
    )
    
    await _send_response(sender, f"{title}\n\n{summary_details}")


async def _send_monthly_summary(sender: str, user_profile: UserProfile) -> None:
    """Send monthly summary for current month."""
    from django.utils import timezone
    from datetime import timedelta
    import calendar
    
    language = user_profile.language
    company = user_profile.company
    
    # Get current month
    today = timezone.now().date()
    month_start = today.replace(day=1)
    _, last_day = calendar.monthrange(today.year, today.month)
    month_end = today.replace(day=last_day)
    
    @sync_to_async
    def _get_month_summary():
        from apps.sales.models import Sale
        from decimal import Decimal
        from collections import defaultdict
        
        sales = Sale.objects.filter(
            company=company,
            created_at__date__gte=month_start,
            created_at__date__lte=month_end,
            status=Sale.Status.CONFIRMED
        ).select_related().prefetch_related('items')
        
        if not sales.exists():
            return t("monthly_summary.no_sales", language=language)
        
        total_by_currency = defaultdict(Decimal)
        sale_count = 0
        product_counts = defaultdict(int)
        weekly_totals = []
        
        for sale in sales:
            sale_count += 1
            for item in sale.items.all():
                if item.unit_price and item.currency:
                    total_by_currency[item.currency] += item.unit_price * item.quantity
                if item.product:
                    product_counts[item.product.name] += item.quantity
        
        best_selling = max(product_counts.items(), key=lambda x: x[1])[0] if product_counts else "N/A"
        
        # Calculate average daily sales
        days_with_sales = sales.values('created_at__date').distinct().count()
        
        # Format summary
        currency_lines = []
        for currency, total in total_by_currency.items():
            currency_lines.append(f"{format_price(total, currency)}")
            
        summary = "Total sales:\n" + "\n".join(currency_lines)
        summary += f"\n\nNumber of sales: {sale_count}"
        summary += f"\nDays with sales: {days_with_sales}"
        summary += f"\nBest-selling item: {best_selling}"
        
        # Calculate average
        if days_with_sales > 0:
            summary += f"\n\n📊 Average per day:"
            for currency, total in total_by_currency.items():
                avg = total / days_with_sales
                summary += f"\n{format_price(avg, currency)}"
        
        return summary
    
    summary_details = await _get_month_summary()
    
    title = t(
        "monthly_summary.title",
        language=language,
        month=today.strftime("%B"),
        year=today.year
    )
    
    await _send_response(sender, f"{title}\n\n{summary_details}")


@sync_to_async
def _update_user_profile(profile_id: int, **kwargs) -> None:
    """Update user profile fields."""
    UserProfile.objects.filter(id=profile_id).update(**kwargs)


@sync_to_async
def _create_products_from_items(items: list, company, currency: str | None = None) -> None:
    """Create products from parsed item list if they don't exist."""
    from apps.catalog.models import Product, ProductPrice

    for item in items:
        # Get or create product
        product, created = Product.objects.get_or_create(
            name=item.product_name,
            company=company,
            defaults={"active": True}
        )

        # Create price if provided 
        if item.unit_price is not None:
            price_currency = currency or company.currency
            ProductPrice.objects.update_or_create(
                product=product,
                currency=price_currency,
                defaults={"amount": item.unit_price}
            )


async def _handle_add_assistant(
    sender: str,
    text: str,
    user_profile: UserProfile | None,
    result,  # UnifiedMessageResult
) -> None:
    """Handle a request to add an assistant."""
    language = user_profile.language if user_profile else "en"
    
    # Check if user is an owner
    if not user_profile or user_profile.role not in [UserProfile.Role.OWNER, UserProfile.Role.BOTH]:
        await _send_response(sender, t("assistant.not_owner", language=language))
        return

    # Get phone number from unified result
    phone_number = result.phone_number
    if not phone_number:
        await _send_response(sender, t("assistant.missing_phone", language=language))
        return

    # Check if phone number is already in use
    existing_profile = await _get_profile_by_phone(phone_number)
    if existing_profile:
        await _send_response(
            sender, t("assistant.already_registered", language=language, phone=phone_number)
        )
        return

    # Create user and profile for the assistant
    assistant_profile = await _create_assistant(phone_number, user_profile.company)

    # Notify the owner
    await _send_response(
        sender,
        t("assistant.added", language=language, phone=phone_number, company=user_profile.company.name),
    )


@sync_to_async
def _get_profile_by_phone(phone_number: str) -> UserProfile | None:
    """Get a user profile by phone number."""
    try:
        return UserProfile.objects.get(phone_number=phone_number)
    except UserProfile.DoesNotExist:
        return None


@sync_to_async
def _create_assistant(phone_number: str, company) -> UserProfile:
    """Create a new assistant user and profile."""
    # Create user (username from phone, removing non-alphanumeric)
    username = "".join(c for c in phone_number if c.isalnum())

    # Ensure unique username
    base_username = username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{counter}"
        counter += 1

    user = User.objects.create_user(username=username)

    # Create profile as assistant
    profile = UserProfile.objects.create(
        user=user,
        company=company,
        role=UserProfile.Role.ASSISTANT,
        phone_number=phone_number,
    )
    return profile


@sync_to_async
def _update_company_currency(company_id: int, currency: str) -> None:
    """Update the company's currency setting."""
    from apps.core.models import Company
    Company.objects.filter(id=company_id).update(currency=currency)


async def _process_sale_message_unified(
    message_id: str,
    sender: str,
    text: str,
    company: "Company | None",
    result,  # UnifiedMessageResult
    is_from_audio: bool = False,
    language: str = "en",
) -> None:
    """
    Process sale using already-parsed result.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        text: The message text or transcription
        company: The company associated with the sender
        result: The unified parsing result with intent and extracted data
        is_from_audio: Whether this message came from audio transcription
        language: User's preferred language for responses
    """
    if not result.items:
        # No items found
        if is_from_audio:
            response = t("sale.no_products_audio", language=language, text=text)
        else:
            response = t("sale.no_products", language=language)
        await _send_response(sender, response)
        return

    # Update company currency if detected
    if result.currency and company:
        await _update_company_currency(company.id, result.currency)
        company.currency = result.currency

    # Create sale
    sale_result = await _create_sale(result.items, message_id, company, currency=result.currency)
    sale = sale_result["sale"]
    unmatched = sale_result["unmatched_items"]

    # Build response message
    response_lines = []

    # Add "I heard you say..." prefix for audio messages
    if is_from_audio:
        response_lines.append(t("sale.heard_prefix", language=language, text=text))
        response_lines.append("")

    sale_items = await _get_sale_items(sale)
    has_missing_prices = False
    currencies_in_sale = set()

    if sale_items:
        response_lines.append("")
        for item in sale_items:
            if item.unit_price is not None and item.currency:
                item_currency = item.currency
                currencies_in_sale.add(item_currency)
                response_lines.append(t("sale.item_with_price", language=language, quantity=item.quantity, product=item.product.name, price=format_price(item.unit_price, item_currency)))
            else:
                response_lines.append(t("sale.item_no_price", language=language, quantity=item.quantity, product=item.product.name))
                has_missing_prices = True

        # Only show total if all items have prices AND all are the same currency
        if not has_missing_prices and len(currencies_in_sale) == 1:
            sale_currency = currencies_in_sale.pop()
            response_lines.append(t("sale.total", language=language, total=format_price(sale.total_amount, sale_currency)))
        elif has_missing_prices:
            response_lines.append(t("sale.missing_prices_note", language=language))

    if unmatched:
        response_lines.append(t("sale.unmatched", language=language, items=", ".join(unmatched)))

    # Send with confirm/cancel buttons, replying to the original message
    buttons = [
        {"id": f"confirm_{sale.id}", "title": t("sale.btn_confirm", language=language)},
        {"id": f"cancel_{sale.id}", "title": t("sale.btn_cancel", language=language)},
    ]
    message_sid = await _send_response_with_buttons(
        sender, "\n".join(response_lines), buttons, reply_to=message_id
    )

    # Store the confirmation message SID for lookup when button is clicked
    if message_sid:
        await _store_confirmation_sid(sale.id, message_sid)


async def _send_response(to: str, message: str) -> None:
    """Send a response message back to the sender."""
    client = WhatsAppClient()
    await client.send_message(to, message)


async def _send_response_with_buttons(
    to: str, message: str, buttons: list[dict[str, str]], reply_to: str | None = None
) -> str | None:
    """Send a response message with buttons back to the sender."""
    client = WhatsAppClient()
    return await client.send_message_with_buttons(to, message, buttons, reply_to=reply_to)


@sync_to_async
def _store_confirmation_sid(sale_id: int, message_sid: str) -> None:
    """Store the confirmation message SID on the sale."""
    Sale.objects.filter(id=sale_id).update(confirmation_message_sid=message_sid)


def handle_sale_confirmation(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Handle a sale confirmation/cancellation from WhatsApp button click.

    Args:
        action: "confirm" or "cancel"
        sender: The sender's phone number (e.g., whatsapp:+1234567890)
        original_message_sid: The SID of the message being replied to
    """
    try:
        close_old_connections()
        asyncio.run(_process_sale_confirmation_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling sale {action}: {e}")


@sync_to_async
def _get_and_update_sale(original_message_sid: str | None, new_status: str) -> tuple[Sale | None, str]:
    """Find the sale by confirmation message SID and update its status."""
    if not original_message_sid:
        logger.warning("No original message SID provided")
        return None, "en"

    try:
        sale = Sale.objects.select_related('items__product', 'company', 'items__product__company', 'items__product__company__members__user').get(
            confirmation_message_sid=original_message_sid,
            status=Sale.Status.PENDING,
        )
        sale.status = new_status
        sale.save(update_fields=["status"])
        
        # Get language from the sale's company members (find the user who made the sale)
        # Since we don't have direct user reference on sale, default to English
        language = "en"
        
        return sale, language
    except Sale.DoesNotExist:
        logger.warning(f"No pending sale found for message SID: {original_message_sid}")
        return None, "en"


async def _process_sale_confirmation_async(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Async processing of sale confirmation.

    Args:
        action: "confirm" or "cancel"
        sender: The sender's phone number
        original_message_sid: The SID of the message being replied to
    """
    # Get user's language preference
    phone_number = _extract_phone_number(sender)
    
    @sync_to_async
    def _get_user_language():
        try:
            profile = UserProfile.objects.get(phone_number=phone_number)
            return profile.language
        except UserProfile.DoesNotExist:
            return "en"
    
    language = await _get_user_language()
    
    if action == "confirm":
        new_status = Sale.Status.CONFIRMED
        response_key = "sale.confirmed"
    else:
        new_status = Sale.Status.CANCELLED
        response_key = "sale.cancelled"

    sale, _ = await _get_and_update_sale(original_message_sid, new_status)

    if sale:
        await _send_response(sender, t(response_key, language=language))
    else:
        await _send_response(sender, t("sale.already_processed", language=language))


def handle_waitlist_confirmation(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Handle a waitlist approval/rejection from WhatsApp button click.

    Args:
        action: "approve" or "reject"
        sender: The sender's phone number (e.g., whatsapp:+1234567890)
        original_message_sid: The SID of the message being replied to
    """
    try:
        close_old_connections()
        asyncio.run(_process_waitlist_confirmation_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling waitlist {action}: {e}")


@sync_to_async
def _get_and_update_waitlist_entry(original_message_sid: str | None, action: str) -> WaitlistEntry | None:
    """Find the waitlist entry by confirmation message SID and update its status."""
    if not original_message_sid:
        logger.warning("No original message SID provided for waitlist confirmation")
        return None

    try:
        entry = WaitlistEntry.objects.get(
            confirmation_message_sid=original_message_sid,
            status=WaitlistEntry.Status.PENDING,
        )
        if action == "approve":
            entry.status = WaitlistEntry.Status.APPROVED
        else:
            entry.status = WaitlistEntry.Status.REJECTED
        entry.save(update_fields=["status"])
        return entry
    except WaitlistEntry.DoesNotExist:
        logger.warning(f"No pending waitlist entry found for message SID: {original_message_sid}")
        return None


@sync_to_async
def _approve_waitlist_entry(entry: WaitlistEntry) -> tuple[Company, UserProfile]:
    """Run the full approval logic for a waitlist entry."""
    # Create company name from entry or generate fallback
    company_name = entry.company_name.strip() if entry.company_name else "Unnamed Shop"

    # Generate unique slug
    base_slug = slugify(company_name)
    slug = base_slug or "shop"
    counter = 1
    while Company.objects.filter(slug=slug).exists():
        slug = f"{base_slug or 'shop'}-{counter}"
        counter += 1

    # Create company
    company = Company.objects.create(name=company_name, slug=slug)

    # Create user (username from phone, removing non-alphanumeric)
    username = "".join(c for c in entry.phone_number if c.isalnum())

    # Ensure unique username
    base_username = username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{counter}"
        counter += 1

    user = User.objects.create_user(username=username)

    # Create user profile as owner
    profile = UserProfile.objects.create(
        user=user,
        company=company,
        role=UserProfile.Role.OWNER,
        phone_number=entry.phone_number,
    )

    # Update waitlist entry
    entry.approved_at = timezone.now()
    entry.company = company
    entry.user_profile = profile
    entry.save(update_fields=["approved_at", "company", "user_profile"])

    return company, profile


async def _process_waitlist_confirmation_async(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Async processing of waitlist confirmation.

    Args:
        action: "approve" or "reject"
        sender: The sender's phone number
        original_message_sid: The SID of the message being replied to
    """
    entry = await _get_and_update_waitlist_entry(original_message_sid, action)

    if not entry:
        await _send_response(sender, t("waitlist.already_processed", language="en"))
        return

    if action == "approve":
        # Run the full approval logic
        company, profile = await _approve_waitlist_entry(entry)

        # Notify admin (in English)
        await _send_response(
            sender,
            t("waitlist_admin.approved", language="en", phone=entry.phone_number, company=company.name),
        )

        # Start onboarding flow - language selection (always start in English)
        await _send_response(
            entry.phone_number,
            t("onboarding.language_selection", language="en"),
        )
    else:
        # Notify admin (in English)
        await _send_response(
            sender,
            t("waitlist_admin.rejected", language="en", phone=entry.phone_number),
        )

        # Notify the user (default English)
        await _send_response(entry.phone_number, t("waitlist.rejected", language="en"))

