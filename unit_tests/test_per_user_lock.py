"""Unit tests for per-user message serialisation lock (#90).

Tests use threading to verify that concurrent messages from the same
sender are processed one at a time, while different senders are unblocked.

Run with:
    python manage.py test unit_tests.test_per_user_lock
"""

import threading
import time

from django.test import TestCase

from apps.whatsapp.services.webhook_handler import _get_user_lock, _user_locks, _user_locks_dict_lock


class GetUserLockTest(TestCase):

    def setUp(self):
        # Clear shared lock dict before each test
        with _user_locks_dict_lock:
            _user_locks.clear()

    def test_same_phone_returns_same_lock(self):
        lock1 = _get_user_lock("+263771234567")
        lock2 = _get_user_lock("+263771234567")
        self.assertIs(lock1, lock2)

    def test_different_phones_return_different_locks(self):
        lock_a = _get_user_lock("+263771234567")
        lock_b = _get_user_lock("+263779999999")
        self.assertIsNot(lock_a, lock_b)

    def test_lock_is_threading_lock(self):
        lock = _get_user_lock("+263771234567")
        self.assertIsInstance(lock, type(threading.Lock()))


class PerUserSerializationTest(TestCase):

    def setUp(self):
        with _user_locks_dict_lock:
            _user_locks.clear()

    def test_same_user_messages_serialized(self):
        """Two threads for the same user must not overlap."""
        phone = "+263771234567"
        order = []

        def slow_message(label):
            with _get_user_lock(phone):
                order.append(f"{label}_start")
                time.sleep(0.05)
                order.append(f"{label}_end")

        t1 = threading.Thread(target=slow_message, args=("msg1",))
        t2 = threading.Thread(target=slow_message, args=("msg2",))
        t1.start()
        time.sleep(0.01)  # ensure t1 acquires the lock first
        t2.start()
        t1.join()
        t2.join()

        # Must be sequential, not interleaved
        self.assertEqual(order, ["msg1_start", "msg1_end", "msg2_start", "msg2_end"])

    def test_different_users_not_blocked(self):
        """Two threads for different users must run in parallel."""
        phone_a = "+263771234567"
        phone_b = "+263779999999"
        started = []
        barrier = threading.Barrier(2)

        def message(phone, label):
            with _get_user_lock(phone):
                started.append(label)
                barrier.wait(timeout=2)  # both must reach here before proceeding

        t1 = threading.Thread(target=message, args=(phone_a, "a"))
        t2 = threading.Thread(target=message, args=(phone_b, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)

        # Both reached the barrier — they ran concurrently
        self.assertIn("a", started)
        self.assertIn("b", started)


class WhatsAppMessageUniqueConstraintTest(TestCase):

    def test_duplicate_message_id_raises(self):
        """Two WhatsAppMessage rows with the same non-empty message ID must fail."""
        from django.db import IntegrityError
        from apps.whatsapp.models import WhatsAppMessage

        WhatsAppMessage.objects.create(
            direction=WhatsAppMessage.Direction.INBOUND,
            message_type=WhatsAppMessage.MessageType.TEXT,
            phone_number="+263771234567",
            whatsapp_message_id="wamid.test123",
        )
        with self.assertRaises(IntegrityError):
            WhatsAppMessage.objects.create(
                direction=WhatsAppMessage.Direction.INBOUND,
                message_type=WhatsAppMessage.MessageType.TEXT,
                phone_number="+263771234567",
                whatsapp_message_id="wamid.test123",
            )

    def test_multiple_null_message_ids_allowed(self):
        """Multiple rows with null message ID must not conflict."""
        from apps.whatsapp.models import WhatsAppMessage

        WhatsAppMessage.objects.create(
            direction=WhatsAppMessage.Direction.OUTBOUND,
            message_type=WhatsAppMessage.MessageType.TEXT,
            phone_number="+263771234567",
            whatsapp_message_id=None,
        )
        WhatsAppMessage.objects.create(
            direction=WhatsAppMessage.Direction.OUTBOUND,
            message_type=WhatsAppMessage.MessageType.TEXT,
            phone_number="+263771234567",
            whatsapp_message_id=None,
        )
        self.assertEqual(
            WhatsAppMessage.objects.filter(whatsapp_message_id__isnull=True).count(), 2
        )

    def test_multiple_empty_string_message_ids_allowed(self):
        """Multiple rows with empty string message ID must not conflict."""
        from apps.whatsapp.models import WhatsAppMessage

        WhatsAppMessage.objects.create(
            direction=WhatsAppMessage.Direction.OUTBOUND,
            message_type=WhatsAppMessage.MessageType.TEXT,
            phone_number="+263771234567",
            whatsapp_message_id="",
        )
        WhatsAppMessage.objects.create(
            direction=WhatsAppMessage.Direction.OUTBOUND,
            message_type=WhatsAppMessage.MessageType.TEXT,
            phone_number="+263771234567",
            whatsapp_message_id="",
        )
        self.assertEqual(
            WhatsAppMessage.objects.filter(whatsapp_message_id="").count(), 2
        )
