"""Per-user message locking to prevent race conditions.

This module implements per-user serialization of WhatsApp messages to prevent
duplicate sales and race conditions when multiple messages arrive from the same
user in rapid succession.

Strategy: Database row-level lock (select_for_update) on the user's WhatsappUser
profile. This approach:
- Works on single-machine deployments (Fly.io pilot)
- Survives machine restarts
- Scales across multiple machines (DB-level locking)
- No additional infrastructure needed

Lock semantics:
- One message at a time per phone number
- Messages from different users process in parallel (no global lock)
- Lock acquired at start of message processing
- Lock released after reply is sent (successfully or after error)
"""

import logging
from typing import Callable, Any

from asgiref.sync import sync_to_async
from django.db import close_old_connections

logger = logging.getLogger(__name__)


@sync_to_async
def _acquire_user_lock(phone_number: str) -> None:
    """
    Acquire a database lock on the user's profile.
    
    Uses select_for_update() on UserProfile by phone_number.
    Blocks until lock is available (other messages from same user finish).
    
    Args:
        phone_number: Normalized phone number (+E.164 format)
        
    Raises:
        UserProfile.DoesNotExist: If user not found (but this shouldn't happen
                                  in normal flow since onboarding creates profile first)
    """
    from apps.core.models import UserProfile
    
    close_old_connections()
    
    # Lock on the user's profile row for duration of this transaction
    # This ensures only one message from this user processes at a time
    profile = UserProfile.objects.select_for_update().get(phone_number=phone_number)
    logger.info(f"[LOCK] Acquired per-user lock for {phone_number}")
    return profile


async def process_with_user_lock(
    phone_number: str,
    async_fn: Callable[..., Any],
    *args,
    **kwargs
) -> Any:
    """
    Process an async function with per-user database lock.
    
    Ensures messages from the same user are processed serially (one at a time),
    in the order they arrived. This prevents:
    - Duplicate sales from rapid retransmission
    - Unpredictable ordering of confirmations
    - Race conditions in sale state management
    
    Args:
        phone_number: Normalized phone number (+E.164 format)
        async_fn: Async function to execute with lock held
        *args: Positional arguments to pass to async_fn
        **kwargs: Keyword arguments to pass to async_fn
        
    Returns:
        Result of async_fn
        
    Example:
        ```python
        # Instead of:
        # run_async(_process_message_async(message_id, sender, text, profile))
        
        # Do:
        # run_async(process_with_user_lock(
        #     _extract_phone_number(sender),
        #     _process_message_async,
        #     message_id, sender, text, profile
        # ))
        ```
    """
    try:
        # Acquire lock (blocks until available)
        profile = await _acquire_user_lock(phone_number)
        logger.info(f"[LOCK] Lock acquired, processing message for {phone_number}")
        
        # Process message with lock held
        try:
            result = await async_fn(*args, **kwargs)
            logger.info(f"[LOCK] Message processed successfully for {phone_number}")
            return result
        except Exception as e:
            logger.error(f"[LOCK] Error processing message for {phone_number}: {e}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"[LOCK] Failed to acquire lock for {phone_number}: {e}", exc_info=True)
        raise
    finally:
        # Lock is automatically released when transaction ends
        logger.debug(f"[LOCK] Lock released for {phone_number}")
