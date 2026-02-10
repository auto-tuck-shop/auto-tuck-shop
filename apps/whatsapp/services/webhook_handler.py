"""Service for handling incoming WhatsApp messages."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
import django.db.utils
from django.db import close_old_connections, connections
import functools
from django.utils import timezone
from django.utils.text import slugify

from apps.core.currencies import format_price
from apps.core.models import Company, UserProfile, WaitlistEntry
from apps.sales.models import Sale
from apps.sales.services import create_sale_from_parsed_items, PriceOverflowError
from apps.whatsapp.services.message_parser import parse_message_unified
from apps.whatsapp.services.whatsapp_client import get_whatsapp_client
from services.openrouter.client import OpenRouterError
from utils.timing import start_tracking, end_tracking, track

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def run_async(coro):
    """
    Run an async coroutine, handling both sync and async contexts.

    In async contexts (like async tests), we can't use asyncio.run() because
    there's already an event loop. We also can't just create a task because
    it won't complete before the caller returns. Instead, we need to run it
    in a thread pool to avoid blocking.

    In sync contexts (like Django views), runs with asyncio.run().
    """
    try:
        # Check if event loop is already running
        loop = asyncio.get_running_loop()
        logger.info(f"[DEBUG] Event loop detected, using thread pool")
        # Event loop exists - can't use asyncio.run()
        # Run in thread pool instead to avoid blocking the event loop
        import concurrent.futures
        from django.conf import settings

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            # In test mode, wait for completion so database writes happen
            # before assertions run
            if 'test' in settings.DATABASES['default']['NAME']:
                logger.info(f"[DEBUG] Test mode detected, waiting for thread pool")
                future.result(timeout=10)  # Wait up to 10 seconds
                logger.info(f"[DEBUG] Thread pool completed")
    except RuntimeError:
        # No event loop running - create one and run
        logger.info(f"[DEBUG] No event loop, using asyncio.run")
        asyncio.run(coro)


def db_sync_to_async(func):
    """Like sync_to_async but closes stale DB connections first.

    Django stores DB connections in thread-local storage. sync_to_async runs
    code in an executor thread that may hold a stale connection from a previous
    request. close_old_connections() ensures we don't use a dead connection.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        close_old_connections()
        return func(*args, **kwargs)
    return sync_to_async(wrapper)


# Load localization strings
_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_ALL_STRINGS = {
    "en": json.loads((_LOCALES_DIR / "en.json").read_text()),
    "sn": json.loads((_LOCALES_DIR / "sn.json").read_text()),
}
DEFAULT_LANGUAGE = "sn"


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """Get localized string by dot-notation key, with optional format args."""
    strings = _ALL_STRINGS.get(lang, _ALL_STRINGS[DEFAULT_LANGUAGE])
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


@db_sync_to_async
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
    print(f"[DEBUG HANDLER] handle_new_waitlist_entry called for {sender}", flush=True)
    try:
        close_old_connections()
        print(f"[DEBUG HANDLER] About to call run_async", flush=True)
        run_async(_process_new_waitlist_entry_async(sender, text))
        print(f"[DEBUG HANDLER] run_async returned", flush=True)
    except Exception as e:
        print(f"[DEBUG HANDLER] Exception: {e}", flush=True)
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
        run_async(_process_waitlisted_message_async(sender, text, waitlist_entry))
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
        run_async(_process_message_async(message_id, sender, text, user_profile))
    except django.db.utils.OperationalError:
        logger.exception(f"DB connection error for message {message_id}, retrying...")
        for conn in connections.all():
            conn.close()
        run_async(_process_message_async(message_id, sender, text, user_profile))
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
        run_async(_process_audio_message_async(message_id, sender, media_id, user_profile))
    except django.db.utils.OperationalError:
        logger.exception(f"DB connection error for audio message {message_id}, retrying...")
        for conn in connections.all():
            conn.close()
        run_async(_process_audio_message_async(message_id, sender, media_id, user_profile))
    except Exception as e:
        logger.exception(f"Error handling audio message {message_id}: {e}")


async def _process_new_waitlist_entry_async(sender: str, text: str) -> None:
    """Add a new user to the waitlist and send language choice buttons."""
    logger.info(f"[DEBUG] _process_new_waitlist_entry_async started for {sender}")
    phone_number = _extract_phone_number(sender)
    logger.info(f"[DEBUG] Phone number: {phone_number}")

    # Create waitlist entry
    logger.info(f"[DEBUG] About to create waitlist entry")
    entry = await _create_waitlist_entry(phone_number, text)
    logger.info(f"[DEBUG] Waitlist entry created: {entry.id if entry else 'None'}")

    # Send language choice buttons (bilingual prompt since user hasn't chosen yet)
    buttons = [
        {"id": f"lang_en_{entry.id}", "title": t("language.btn_en")},
        {"id": f"lang_sn_{entry.id}", "title": t("language.btn_sn")},
    ]
    message_sid = await _send_response_with_buttons(
        sender, t("language.prompt"), buttons,
    )

    if message_sid:
        await _store_waitlist_response_message_sid(entry.id, message_sid)

    # Send admin notification with approve/reject buttons
    await _send_waitlist_admin_notification(entry)


def handle_language_button_action(
    lang: str,
    entry_id: int,
    sender: str,
) -> None:
    """Handle a language selection button click from a waitlisted user."""
    try:
        close_old_connections()
        run_async(_process_language_button_async(lang, entry_id, sender))
    except Exception as e:
        logger.exception(f"Error handling language selection for {sender}: {e}")


async def _process_language_button_async(lang: str, entry_id: int, sender: str) -> None:
    """Save language choice and send confirmation + waitlist welcome."""
    await _update_waitlist_language(entry_id, lang)
    await _send_response(sender, t("language.confirmed", lang=lang))
    await _send_response(sender, t("waitlist.welcome", lang=lang))


@db_sync_to_async
def _update_waitlist_language(entry_id: int, language: str) -> None:
    """Update the language on a waitlist entry."""
    WaitlistEntry.objects.filter(id=entry_id).update(language=language)


@db_sync_to_async
def _get_waitlist_language(phone_number: str) -> str:
    """Get the language for a waitlisted user."""
    try:
        entry = WaitlistEntry.objects.get(phone_number=phone_number)
        return entry.language
    except WaitlistEntry.DoesNotExist:
        return DEFAULT_LANGUAGE


async def _process_waitlisted_message_async(sender: str, text: str, waitlist_entry: WaitlistEntry) -> None:
    """Handle a message from a waitlisted user."""
    lang = waitlist_entry.language
    if waitlist_entry.status == WaitlistEntry.Status.PENDING:
        # If they don't have a company name yet, save this message as their company name
        if not waitlist_entry.company_name and text.strip():
            await _update_waitlist_company_name(waitlist_entry.id, text.strip())
            await _send_response(
                sender,
                t("waitlist.shop_name_noted", lang=lang, shop_name=text.strip()),
            )
        else:
            await _send_response(sender, t("waitlist.still_pending", lang=lang))
    elif waitlist_entry.status == WaitlistEntry.Status.REJECTED:
        await _send_response(sender, t("waitlist.rejected", lang=lang))


@db_sync_to_async
def _update_waitlist_company_name(entry_id: int, company_name: str) -> None:
    """Update the company name on a waitlist entry."""
    WaitlistEntry.objects.filter(id=entry_id).update(company_name=company_name)


@db_sync_to_async
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
        phone=entry.phone_number,
        message=entry.first_message[:100] if entry.first_message else "(none)",
    )

    buttons = [
        {"id": f"waitlist_approve_{entry.id}", "title": t("waitlist_admin.btn_approve")},
        {"id": f"waitlist_reject_{entry.id}", "title": t("waitlist_admin.btn_reject")},
    ]

    message_sid = await _send_response_with_buttons(
        ADMIN_PHONE_NUMBER,
        message,
        buttons,
    )

    # Store the response message SID for lookup when button is clicked
    if message_sid:
        await _store_waitlist_response_message_sid(entry.id, message_sid)


@db_sync_to_async
def _store_waitlist_response_message_sid(entry_id: int, message_sid: str) -> None:
    """Store the response message SID on the waitlist entry for button click lookup."""
    WaitlistEntry.objects.filter(id=entry_id).update(confirmation_message_sid=message_sid)


async def _create_sale(parsed_items, message_id, company, currency=None):
    """Create sale from parsed items (async wrapper with timing)."""
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
        company = user_profile.company if user_profile else None
        lang = user_profile.language if user_profile else DEFAULT_LANGUAGE

        # UNIFIED: Single LLM call for intent + extraction
        try:
            result = await parse_message_unified(text, company=company)
        except Exception as e:
            logger.exception(f"LLM processing failed for message {message_id}: {e}")
            await _send_response(sender, t("error.processing_failed", lang=lang))
            return

        logger.info(f"Parsed message - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, text, user_profile, result)
            return

        # Default: treat as sale
        await _process_sale_message_unified(message_id, sender, text, company, result, lang=lang)
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
        company = user_profile.company if user_profile else None
        lang = user_profile.language if user_profile else DEFAULT_LANGUAGE
        phone_number = _extract_phone_number(sender)

        # Step 1: Download audio from Meta CDN (we need the bytes for both transcription and R2)
        whatsapp_client = get_whatsapp_client()
        media_result = await whatsapp_client.download_media(media_id)

        if not media_result:
            await _send_response(sender, t("audio.download_failed", lang=lang))
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
                    @db_sync_to_async
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
            @db_sync_to_async
            def _update_message_transcription(msg_id: str, transcript: str):
                from apps.whatsapp.models import WhatsAppMessage
                WhatsAppMessage.objects.filter(whatsapp_message_id=msg_id).update(
                    transcribed_text=transcript,
                    content=transcript,
                )

            await _update_message_transcription(message_id, transcribed_text)

        except ElevenLabsError as e:
            logger.exception(f"Failed to transcribe audio message {message_id}: {e}")
            await _send_response(sender, t("audio.transcription_failed", lang=lang))
            return

        # Step 4: Unified parse (intent + extraction)
        try:
            result = await parse_message_unified(transcribed_text, company=company)
        except Exception as e:
            logger.exception(f"LLM processing failed for audio message {message_id}: {e}")
            await _send_response(sender, t("error.processing_failed", lang=lang))
            return

        logger.info(f"Parsed audio - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, transcribed_text, user_profile, result)
            return

        # Default: treat as sale
        await _process_sale_message_unified(
            message_id, sender, transcribed_text, company, result, is_from_audio=True, lang=lang
        )
    finally:
        end_tracking()


async def _handle_add_assistant(
    sender: str,
    text: str,
    user_profile: UserProfile | None,
    result,  # UnifiedMessageResult
) -> None:
    """Handle a request to add an assistant."""
    lang = user_profile.language if user_profile else DEFAULT_LANGUAGE

    # Check if user is an owner
    if not user_profile or user_profile.role != UserProfile.Role.OWNER:
        await _send_response(sender, t("assistant.not_owner", lang=lang))
        return

    # Get phone number from unified result
    phone_number = result.phone_number
    if not phone_number:
        await _send_response(sender, t("assistant.missing_phone", lang=lang))
        return

    # Check if phone number is already in use
    existing_profile = await _get_profile_by_phone(phone_number)
    if existing_profile:
        await _send_response(
            sender, t("assistant.already_registered", lang=lang, phone=phone_number)
        )
        return

    # Create user and profile for the assistant
    assistant_profile = await _create_assistant(phone_number, user_profile.company)

    # Notify the owner
    await _send_response(
        sender,
        t("assistant.added", lang=lang, phone=phone_number, company=user_profile.company.name),
    )


@db_sync_to_async
def _get_profile_by_phone(phone_number: str) -> UserProfile | None:
    """Get a user profile by phone number."""
    try:
        return UserProfile.objects.get(phone_number=phone_number)
    except UserProfile.DoesNotExist:
        return None


@db_sync_to_async
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


@db_sync_to_async
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
    lang: str = DEFAULT_LANGUAGE,
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
        lang: The user's preferred language
    """
    if not result.items:
        # No items found
        response = t("sale.no_products", lang=lang)
        await _send_response(sender, response)
        return

    # Update company currency if detected
    if result.currency and company:
        await _update_company_currency(company.id, result.currency)
        company.currency = result.currency

    # Create sale
    try:
        sale_result = await _create_sale(result.items, message_id, company, currency=result.currency)
    except PriceOverflowError:
        logger.warning("Price overflow for message %s from %s", message_id, sender)
        response = t("sale.price_too_large", lang=lang)
        await _send_response(sender, response, reply_to=message_id)
        return
    sale = sale_result["sale"]
    unmatched = sale_result["unmatched_items"]

    # Build response message
    response_lines = []

    sale_items = await _get_sale_items(sale)
    has_missing_prices = False
    currencies_in_sale = set()

    if sale_items:
        for item in sale_items:
            if item.unit_price is not None and item.currency:
                item_currency = item.currency
                currencies_in_sale.add(item_currency)
                response_lines.append(t("sale.item_with_price", lang=lang, quantity=item.quantity, product=item.product.name, price=format_price(item.unit_price, item_currency)))
            else:
                response_lines.append(t("sale.item_no_price", lang=lang, quantity=item.quantity, product=item.product.name))
                has_missing_prices = True

        # Only show total if all items have prices AND all are the same currency
        if not has_missing_prices and len(currencies_in_sale) == 1:
            sale_currency = currencies_in_sale.pop()
            response_lines.append(t("sale.total", lang=lang, total=format_price(sale.total_amount, sale_currency)))
        elif has_missing_prices:
            response_lines.append(t("sale.missing_prices_note", lang=lang))

    if unmatched:
        response_lines.append(t("sale.unmatched", lang=lang, items=", ".join(unmatched)))

    # Send with "Bot mistake?" and "Start Over" buttons
    buttons = [
        {"id": f"mistake_{sale.id}", "title": t("sale.btn_mistake", lang=lang)},
        {"id": f"cancel_{sale.id}", "title": t("sale.btn_cancel", lang=lang)},
    ]
    message_sid = await _send_response_with_buttons(
        sender, "\n".join(response_lines), buttons, reply_to=message_id
    )

    # Store the response message SID for lookup when button is clicked
    if message_sid:
        await _store_response_message_sid(sale.id, message_sid)


async def _send_response(to: str, message: str, reply_to: str | None = None) -> None:
    """Send a response message back to the sender."""
    client = get_whatsapp_client()
    await client.send_message(to, message, reply_to=reply_to)


async def _send_response_with_buttons(
    to: str, message: str, buttons: list[dict[str, str]], reply_to: str | None = None
) -> str | None:
    """Send a response message with buttons back to the sender."""
    client = get_whatsapp_client()
    return await client.send_message_with_buttons(to, message, buttons, reply_to=reply_to)


@db_sync_to_async
def _store_response_message_sid(sale_id: int, message_sid: str) -> None:
    """Store the response message SID on the sale for button click lookup."""
    Sale.objects.filter(id=sale_id).update(confirmation_message_sid=message_sid)


def handle_sale_button_action(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Handle a sale button action (mistake/cancel) from WhatsApp button click.

    Args:
        action: "mistake" or "cancel"
        sender: The sender's phone number (e.g., whatsapp:+1234567890)
        original_message_sid: The SID of the message being replied to
    """
    try:
        close_old_connections()
        run_async(_process_sale_button_action_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling sale {action}: {e}")


@db_sync_to_async
def _get_and_update_sale(original_message_sid: str | None, new_status: str, bot_mistake: bool = False) -> tuple[Sale, str | None] | None:
    """Find the sale by response message SID and update its status.

    Returns:
        A tuple of (sale, whatsapp_message_id) if found, None otherwise.
    """
    if not original_message_sid:
        logger.warning("No original message SID provided")
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
        logger.warning(f"No confirmed sale found for message SID: {original_message_sid}")
        return None


async def _process_sale_button_action_async(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Async processing of sale button action.

    Args:
        action: "mistake" or "cancel"
        sender: The sender's phone number
        original_message_sid: The SID of the message being replied to
    """
    phone_number = _extract_phone_number(sender)
    profile = await _get_profile_by_phone(phone_number)
    lang = profile.language if profile else DEFAULT_LANGUAGE

    bot_mistake = action == "mistake"
    new_status = Sale.Status.CANCELLED
    response_key = "sale.bot_mistake" if bot_mistake else "sale.cancelled"

    result = await _get_and_update_sale(original_message_sid, new_status, bot_mistake=bot_mistake)

    if result:
        sale, original_whatsapp_message_id = result
        await _send_response(
            sender, t(response_key, lang=lang), reply_to=original_whatsapp_message_id
        )
    else:
        await _send_response(sender, t("sale.already_processed", lang=lang))


def handle_waitlist_button_action(
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
        run_async(_process_waitlist_button_action_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling waitlist {action}: {e}")


@db_sync_to_async
def _get_and_update_waitlist_entry(original_message_sid: str | None, action: str) -> WaitlistEntry | None:
    """Find the waitlist entry by response message SID and update its status."""
    if not original_message_sid:
        logger.warning("No original message SID provided for waitlist button action")
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


@db_sync_to_async
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
        language=entry.language,
    )

    # Update waitlist entry
    entry.approved_at = timezone.now()
    entry.company = company
    entry.user_profile = profile
    entry.save(update_fields=["approved_at", "company", "user_profile"])

    return company, profile


async def _process_waitlist_button_action_async(
    action: str,
    sender: str,
    original_message_sid: str | None = None,
) -> None:
    """
    Async processing of waitlist button action.

    Args:
        action: "approve" or "reject"
        sender: The sender's phone number
        original_message_sid: The SID of the message being replied to
    """
    entry = await _get_and_update_waitlist_entry(original_message_sid, action)

    if not entry:
        await _send_response(sender, t("waitlist.already_processed"))
        return

    lang = entry.language

    if action == "approve":
        # Run the full approval logic
        company, profile = await _approve_waitlist_entry(entry)

        # Notify admin
        await _send_response(
            sender,
            t("waitlist_admin.approved", phone=entry.phone_number, company=company.name),
        )

        # Send approval notification to the user in their language
        await _send_response(
            entry.phone_number,
            t("approval.welcome", lang=lang, company=company.name),
        )
    else:
        # Notify admin
        await _send_response(
            sender,
            t("waitlist_admin.rejected", phone=entry.phone_number),
        )

        # Notify the user in their language
        await _send_response(entry.phone_number, t("waitlist.rejected", lang=lang))
