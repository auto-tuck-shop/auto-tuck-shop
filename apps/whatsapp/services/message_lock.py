"""Per-user message locking to prevent race conditions.

Uses a threading.Lock per phone number, acquired synchronously before run_async()
in handle_incoming_message / handle_incoming_audio_message. Because run_async()
blocks until the coroutine finishes, the lock is held for the full processing
duration — serialising concurrent messages from the same sender.

Single-machine only (Fly.io pilot). The DB unique constraint on
whatsapp_message_id handles the Meta webhook retry / duplicate case independently.
"""

import threading

_user_locks: dict[str, threading.Lock] = {}
_user_locks_dict_lock = threading.Lock()


def get_user_lock(phone_number: str) -> threading.Lock:
    """Return a per-user threading.Lock, creating one on first use."""
    with _user_locks_dict_lock:
        if phone_number not in _user_locks:
            _user_locks[phone_number] = threading.Lock()
        return _user_locks[phone_number]
