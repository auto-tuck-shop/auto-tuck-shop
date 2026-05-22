"""Shared utilities and public entry points for WhatsApp message handling.

Business logic lives in:
  - sale_handler.py     — sale creation and sale button actions
  - waitlist_handler.py — onboarding, language selection, approval
  - media_handler.py    — audio download, transcription, R2 upload
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import django.db.utils
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import User
from django.db import close_old_connections, connections
from django.utils import timezone

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


# ---------------------------------------------------------------------------
# Async execution helpers
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine safely from both sync and async contexts."""
    try:
        loop = asyncio.get_running_loop()
        logger.info("[DEBUG] Event loop detected, using thread pool")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            if 'test' in settings.DATABASES['default']['NAME']:
                logger.info("[DEBUG] Test mode detected, waiting for thread pool")
                future.result(timeout=10)
                logger.info("[DEBUG] Thread pool completed")
    except RuntimeError:
        logger.info("[DEBUG] No event loop, using asyncio.run")
        asyncio.run(coro)


def db_sync_to_async(func):
    """Like sync_to_async but closes stale DB connections first."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        close_old_connections()
        return func(*args, **kwargs)
    return sync_to_async(wrapper)


# ---------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------

_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_ALL_STRINGS = {
    "en": json.loads((_LOCALES_DIR / "en.json").read_text()),
    "sn": json.loads((_LOCALES_DIR / "sn.json").read_text()),
}
DEFAULT_LANGUAGE = "sn"


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """Get localised string by dot-notation key, with optional format args."""
    strings = _ALL_STRINGS.get(lang, _ALL_STRINGS[DEFAULT_LANGUAGE])
    keys = key.split(".")
    value = strings
    for k in keys:
        value = value[k]
    return value.format(**kwargs) if kwargs else value


# ---------------------------------------------------------------------------
# Shared constants and helpers
# ---------------------------------------------------------------------------

ADMIN_PHONE_NUMBER = "+14342183470"


def _extract_phone_number(sender: str) -> str:
    """Normalise sender to +E.164 format."""
    if sender.startswith("whatsapp:"):
        sender = sender[9:]
    if not sender.startswith("+"):
        sender = f"+{sender}"
    return sender


@db_sync_to_async
def _get_profile_by_phone(phone_number: str) -> UserProfile | None:
    try:
        return UserProfile.objects.get(phone_number=phone_number)
    except UserProfile.DoesNotExist:
        return None


@db_sync_to_async
def _upload_to_r2(media_data: bytes, media_id: str, mime_type: str, phone_number: str) -> str | None:
    try:
        from services.storage import R2StorageClient
        client = R2StorageClient()
        return client.upload_media(media_data, media_id, mime_type, phone_number)
    except Exception as e:
        logger.error(f"Failed to upload media to R2: {e}", exc_info=True)
        return None


async def _send_response(to: str, message: str, reply_to: str | None = None) -> None:
    client = get_whatsapp_client()
    await client.send_message(to, message, reply_to=reply_to)


async def _send_response_with_buttons(
    to: str, message: str, buttons: list[dict[str, str]], reply_to: str | None = None
) -> str | None:
    client = get_whatsapp_client()
    return await client.send_message_with_buttons(to, message, buttons, reply_to=reply_to)


# ---------------------------------------------------------------------------
# Public entry points (called from views.py)
# ---------------------------------------------------------------------------

def handle_new_waitlist_entry(sender: str, text: str) -> None:
    from apps.whatsapp.services.waitlist_handler import handle_new_waitlist_entry as _handle
    _handle(sender, text)


def handle_waitlisted_message(sender: str, text: str, waitlist_entry: WaitlistEntry) -> None:
    from apps.whatsapp.services.waitlist_handler import handle_waitlisted_message as _handle
    _handle(sender, text, waitlist_entry)


def handle_language_button_action(lang: str, entry_id: int, sender: str) -> None:
    from apps.whatsapp.services.waitlist_handler import handle_language_button_action as _handle
    _handle(lang, entry_id, sender)


def handle_waitlist_button_action(action: str, sender: str, original_message_sid: str | None = None) -> None:
    from apps.whatsapp.services.waitlist_handler import handle_waitlist_button_action as _handle
    _handle(action, sender, original_message_sid)


def handle_sale_button_action(action: str, sender: str, original_message_sid: str | None = None) -> None:
    from apps.whatsapp.services.sale_handler import handle_sale_button_action as _handle
    _handle(action, sender, original_message_sid)


def handle_incoming_message(
    message_id: str,
    sender: str,
    text: str,
    user_profile: UserProfile | None = None,
) -> None:
    try:
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
    try:
        close_old_connections()
        from apps.whatsapp.services.media_handler import process_audio_message_async
        run_async(process_audio_message_async(message_id, sender, media_id, user_profile))
    except django.db.utils.OperationalError:
        logger.exception(f"DB connection error for audio message {message_id}, retrying...")
        for conn in connections.all():
            conn.close()
        from apps.whatsapp.services.media_handler import process_audio_message_async
        run_async(process_audio_message_async(message_id, sender, media_id, user_profile))
    except Exception as e:
        logger.exception(f"Error handling audio message {message_id}: {e}")


async def _process_message_async(
    message_id: str,
    sender: str,
    text: str,
    user_profile: UserProfile | None = None,
) -> None:
    start_tracking(request_id=message_id)
    try:
        company = user_profile.company if user_profile else None
        lang = user_profile.language if user_profile else DEFAULT_LANGUAGE

        try:
            result = await parse_message_unified(text, company=company)
        except Exception as e:
            logger.exception(f"LLM processing failed for message {message_id}: {e}")
            await _send_response(sender, t("error.processing_failed", lang=lang))
            return

        logger.info(f"Parsed message - intent: {result.intent}, confidence: {result.confidence}")

        if result.intent == "add_assistant":
            from apps.whatsapp.services.waitlist_handler import handle_add_assistant
            await handle_add_assistant(sender, text, user_profile, result)
            return

        from apps.whatsapp.services.sale_handler import process_sale_message_unified
        await process_sale_message_unified(message_id, sender, text, company, result, lang=lang)
    finally:
        end_tracking()
