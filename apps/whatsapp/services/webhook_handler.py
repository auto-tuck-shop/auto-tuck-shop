"""Service for handling incoming WhatsApp messages."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify

from apps.core.currencies import format_price
from apps.core.models import Company, UserProfile, WaitlistEntry
from apps.sales.models import Sale
from apps.sales.services import create_sale_from_parsed_items
from apps.whatsapp.services.message_parser import parse_sale_message, detect_message_intent, parse_message_unified
from apps.whatsapp.services.whatsapp_client import WhatsAppClient
from utils.timing import start_tracking, end_tracking, track

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

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
        asyncio.run(_process_audio_message_async(message_id, sender, media_id, user_profile))
    except Exception as e:
        logger.exception(f"Error handling audio message {message_id}: {e}")


async def _process_new_waitlist_entry_async(sender: str, text: str) -> None:
    """Add a new user to the waitlist and send a welcome message."""
    phone_number = _extract_phone_number(sender)

    # Create waitlist entry
    entry = await _create_waitlist_entry(phone_number, text)

    # Send welcome message
    await _send_response(
        sender,
        "Welcome! You've been added to our waitlist.\n\n"
        "An administrator will review your request and approve your account shortly. "
        "We'll send you a message when you're approved.\n\n"
        "In the meantime, if you'd like to provide a name for your shop, "
        "just send another message with it."
    )

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
                f"Thanks! We've noted your shop name as \"{text.strip()}\".\n\n"
                "You're still on our waitlist. An administrator will review your request soon."
            )
        else:
            await _send_response(
                sender,
                "Thanks for your message! You're still on our waitlist.\n\n"
                "An administrator will review your request soon. "
                "We'll notify you when your account is approved."
            )
    elif waitlist_entry.status == WaitlistEntry.Status.REJECTED:
        await _send_response(
            sender,
            "Sorry, your request to join was not approved. "
            "Please contact support if you believe this is an error."
        )


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
    message = (
        f"New waitlist request:\n\n"
        f"Phone: {entry.phone_number}\n"
        f"Message: {entry.first_message[:100] if entry.first_message else '(none)'}"
    )

    buttons = [
        {"id": f"waitlist_approve_{entry.id}", "title": "Confirm"},
        {"id": f"waitlist_reject_{entry.id}", "title": "Cancel"},
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


async def _create_sale(parsed_items, message_id, company):
    """Create sale from parsed items (async wrapper with timing)."""
    async with track("create_sale"):
        @sync_to_async
        def _create_sale_sync():
            return create_sale_from_parsed_items(
                items=parsed_items,
                whatsapp_message_id=message_id,
                company=company,
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
        company = user_profile.company if user_profile else None

        # UNIFIED: Single LLM call for intent + extraction
        result = await parse_message_unified(text)
        logger.info(f"Parsed message - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, text, user_profile, result)
            return

        # Default: treat as sale
        await _process_sale_message_unified(message_id, sender, text, company, result)
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
        phone_number = _extract_phone_number(sender)

        # Step 1: Download audio from Meta CDN (we need the bytes for both transcription and R2)
        whatsapp_client = WhatsAppClient()
        media_result = await whatsapp_client.download_media(media_id)

        if not media_result:
            await _send_response(
                sender,
                "Sorry, I couldn't download your audio message. Please try sending it again or send a text message instead."
            )
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

            # Run both operations in parallel!
            transcription_task = elevenlabs_client.transcribe_audio(audio_data, filename)
            r2_upload_task = _upload_to_r2(audio_data, media_id, mime_type, phone_number)

            # Wait for both to complete
            transcribed_text, r2_url = await asyncio.gather(transcription_task, r2_upload_task)

            logger.info(f"Transcribed audio message {message_id}: {transcribed_text[:100]}...")

            # Step 3: Update database with transcription and R2 URL
            @sync_to_async
            def _update_message_transcription(msg_id: str, transcript: str, r2_url: str | None):
                from apps.whatsapp.models import WhatsAppMessage
                update_fields = {
                    "transcribed_text": transcript,
                    "content": transcript,
                }
                if r2_url:
                    update_fields["r2_media_url"] = r2_url
                WhatsAppMessage.objects.filter(whatsapp_message_id=msg_id).update(**update_fields)

            await _update_message_transcription(message_id, transcribed_text, r2_url)

        except ElevenLabsError as e:
            logger.error(f"Failed to transcribe audio message {message_id}: {e}")
            await _send_response(
                sender,
                "Sorry, I couldn't transcribe your audio message. Please try sending a text message instead."
            )
            return

        # Step 4: Unified parse (intent + extraction)
        result = await parse_message_unified(transcribed_text)
        logger.info(f"Parsed audio - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            await _handle_add_assistant(sender, transcribed_text, user_profile, result)
            return

        # Default: treat as sale
        await _process_sale_message_unified(
            message_id, sender, transcribed_text, company, result, is_from_audio=True
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
    # Check if user is an owner
    if not user_profile or user_profile.role != UserProfile.Role.OWNER:
        await _send_response(
            sender,
            "Sorry, only shop owners can add assistants. "
            "Contact the shop owner if you need to add team members."
        )
        return

    # Get phone number from unified result
    phone_number = result.phone_number
    if not phone_number:
        await _send_response(
            sender,
            "I couldn't find a phone number in your message. "
            "Please include the assistant's phone number, e.g., "
            "'add assistant +27821234567'"
        )
        return

    # Check if phone number is already in use
    existing_profile = await _get_profile_by_phone(phone_number)
    if existing_profile:
        await _send_response(
            sender,
            f"The phone number {phone_number} is already registered to another user."
        )
        return

    # Create user and profile for the assistant
    assistant_profile = await _create_assistant(phone_number, user_profile.company)

    # Notify the owner
    await _send_response(
        sender,
        f"Assistant added successfully!\n\n"
        f"Phone: {phone_number}\n"
        f"They can now send sales messages for {user_profile.company.name}."
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


async def _process_sale_message(
    message_id: str,
    sender: str,
    text: str,
    company: "Company | None" = None,
) -> None:
    """Process a sale message."""
    await _process_sale_message_from_audio(message_id, sender, text, company, is_from_audio=False)


async def _process_sale_message_unified(
    message_id: str,
    sender: str,
    text: str,
    company: "Company | None",
    result,  # UnifiedMessageResult
    is_from_audio: bool = False,
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
    """
    if not result.items:
        # No items found
        if is_from_audio:
            response = f'I heard you say "{text}"\n\nI couldn\'t identify any products. Please list items, e.g., "2 cokes, 1 chips"'
        else:
            response = 'I couldn\'t identify any products. Please list items, e.g., "2 cokes, 1 chips"'
        await _send_response(sender, response)
        return

    # Update company currency if detected
    if result.currency and company:
        await _update_company_currency(company.id, result.currency)
        company.currency = result.currency

    # Create sale
    sale_result = await _create_sale(result.items, message_id, company)
    sale = sale_result["sale"]
    unmatched = sale_result["unmatched_items"]

    # Build response message
    response_lines = []

    # Add "I heard you say..." prefix for audio messages
    if is_from_audio:
        response_lines.append(f'I heard you say "{text}"')
        response_lines.append("")

    sale_items = await _get_sale_items(sale)
    currency = company.currency if company else "USD"
    has_missing_prices = False
    if sale_items:
        response_lines.append("")
        for item in sale_items:
            if item.unit_price is not None:
                response_lines.append(f"  {item.quantity}x {item.product.name} @ {format_price(item.unit_price, currency)}")
            else:
                response_lines.append(f"  {item.quantity}x {item.product.name} (no price)")
                has_missing_prices = True
        response_lines.append(f"\nTotal: {format_price(sale.total_amount, currency)}")
        if has_missing_prices:
            response_lines.append('\nNote: Some items have no price set. Include prices (e.g. "2x Pear @ 5") to see totals.')

    if unmatched:
        response_lines.append(f"\n⚠ Unmatched: {', '.join(unmatched)}")

    # Send with confirm/cancel buttons, replying to the original message
    buttons = [
        {"id": f"confirm_{sale.id}", "title": "Confirm"},
        {"id": f"cancel_{sale.id}", "title": "Cancel"},
    ]
    message_sid = await _send_response_with_buttons(
        sender, "\n".join(response_lines), buttons, reply_to=message_id
    )

    # Store the confirmation message SID for lookup when button is clicked
    if message_sid:
        await _store_confirmation_sid(sale.id, message_sid)


async def _process_sale_message_from_audio(
    message_id: str,
    sender: str,
    text: str,
    company: "Company | None" = None,
    is_from_audio: bool = False,
) -> None:
    """
    Process sale message, optionally from audio transcription.

    Args:
        message_id: The WhatsApp message ID
        sender: The sender's phone number
        text: The message text or transcription
        company: The company associated with the sender
        is_from_audio: Whether this message came from audio transcription
    """
    # Parse the sale message using LLM
    parsed_result = await parse_sale_message(text)

    if not parsed_result.items:
        # No items found - send a helpful response
        if is_from_audio:
            response = (
                f'I heard you say "{text}"\n\n'
                "I couldn't identify any products in your message. "
                "Please list the items you sold, e.g., '2 cokes, 1 chips'"
            )
        else:
            response = (
                "I couldn't identify any products in your message. "
                "Please list the items you sold, e.g., '2 cokes, 1 chips'"
            )
        await _send_response(sender, response)
        return

    # Update company currency if detected
    if parsed_result.currency and company:
        await _update_company_currency(company.id, parsed_result.currency)
        company.currency = parsed_result.currency  # Update local reference

    # Create the sale
    result = await _create_sale(parsed_result.items, message_id, company)

    sale = result["sale"]
    unmatched = result["unmatched_items"]

    # Build response message
    response_lines = []

    # Add "I heard you say..." prefix for audio messages
    if is_from_audio:
        response_lines.append(f'I heard you say "{text}"')
        response_lines.append("")

    sale_items = await _get_sale_items(sale)
    currency = company.currency if company else "USD"
    has_missing_prices = False
    if sale_items:
        response_lines.append("")
        for item in sale_items:
            if item.unit_price is not None:
                response_lines.append(f"  {item.quantity}x {item.product.name} @ {format_price(item.unit_price, currency)}")
            else:
                response_lines.append(f"  {item.quantity}x {item.product.name} (no price)")
                has_missing_prices = True
        response_lines.append(f"\nTotal: {format_price(sale.total_amount, currency)}")
        if has_missing_prices:
            response_lines.append('\nNote: Some items have no price set. Include prices (e.g. "2x Pear @ 5") to see totals.')

    if unmatched:
        response_lines.append(f"\n⚠ Unmatched: {', '.join(unmatched)}")

    # Send with confirm/cancel buttons, replying to the original message
    buttons = [
        {"id": f"confirm_{sale.id}", "title": "Confirm"},
        {"id": f"cancel_{sale.id}", "title": "Cancel"},
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
        asyncio.run(_process_sale_confirmation_async(action, sender, original_message_sid))
    except Exception as e:
        logger.exception(f"Error handling sale {action}: {e}")


@sync_to_async
def _get_and_update_sale(original_message_sid: str | None, new_status: str) -> Sale | None:
    """Find the sale by confirmation message SID and update its status."""
    if not original_message_sid:
        logger.warning("No original message SID provided")
        return None

    try:
        sale = Sale.objects.get(
            confirmation_message_sid=original_message_sid,
            status=Sale.Status.PENDING,
        )
        sale.status = new_status
        sale.save(update_fields=["status"])
        return sale
    except Sale.DoesNotExist:
        logger.warning(f"No pending sale found for message SID: {original_message_sid}")
        return None


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
    if action == "confirm":
        new_status = Sale.Status.CONFIRMED
        status_text = "confirmed"
        emoji = "✓"
    else:
        new_status = Sale.Status.CANCELLED
        status_text = "cancelled"
        emoji = "✗"

    sale = await _get_and_update_sale(original_message_sid, new_status)

    if sale:
        await _send_response(
            sender,
            f"{emoji} {status_text.capitalize()}",
        )
    else:
        await _send_response(
            sender,
            "Sale already processed or not found",
        )


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
        await _send_response(
            sender,
            "Waitlist entry already processed or not found",
        )
        return

    if action == "approve":
        # Run the full approval logic
        company, profile = await _approve_waitlist_entry(entry)

        # Notify admin
        await _send_response(
            sender,
            f"✓ Approved {entry.phone_number}\nCompany: {company.name}",
        )

        # Send approval notification to the user
        await _send_response(
            entry.phone_number,
            f"Welcome to Auto Tuck Shop! Your account has been approved.\n\n"
            f"Company: {company.name}\n\n"
            f"You can now send sales messages to track your sales. For example:\n"
            f"'sold 2 cokes R15 each, 1 chips R10'\n"
            f"'3 waters R12 each, 2 chocolates R8 each'\n\n"
            f"As an owner, you can also add assistants by sending messages like:\n"
            f"'add assistant +27821234567'"
        )
    else:
        # Notify admin
        await _send_response(
            sender,
            f"✗ Rejected {entry.phone_number}",
        )

        # Optionally notify the user
        await _send_response(
            entry.phone_number,
            "Sorry, your request to join was not approved. "
            "Please contact support if you believe this is an error."
        )
