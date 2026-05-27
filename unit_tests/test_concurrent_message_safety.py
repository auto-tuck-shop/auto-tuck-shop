"""Unit tests for per-user message serialisation via threading.Lock."""

import threading
import time

from django.test import TransactionTestCase
from django.contrib.auth.models import User
from unittest.mock import patch

from apps.core.models import Company, UserProfile
from apps.whatsapp.services.message_lock import get_user_lock


class GetUserLockTest(TransactionTestCase):

    def test_same_phone_returns_same_lock(self):
        lock1 = get_user_lock("+263111111111")
        lock2 = get_user_lock("+263111111111")
        self.assertIs(lock1, lock2)

    def test_different_phones_return_different_locks(self):
        lock1 = get_user_lock("+263111111111")
        lock2 = get_user_lock("+263222222222")
        self.assertIsNot(lock1, lock2)


class MessageSerializationTest(TransactionTestCase):
    """Verify that handle_incoming_message serialises concurrent calls per user."""

    def setUp(self):
        company = Company.objects.create(name="Test Shop", slug="test-shop-ms")
        user = User.objects.create_user("testuser_ms", "ms@example.com", "password")
        UserProfile.objects.create(
            user=user,
            company=company,
            phone_number="+263100000001",
            language="en",
        )

    def test_same_user_messages_serialise(self):
        """Two concurrent messages from the same user must not overlap."""
        events = []
        call_count = [0]

        def fake_run_async(coro):
            coro.close()  # prevent "coroutine was never awaited" warning
            n = call_count[0]
            call_count[0] += 1
            events.append(f"msg{n}:start")
            time.sleep(0.05)
            events.append(f"msg{n}:end")

        with patch("apps.whatsapp.services.webhook_handler.run_async", side_effect=fake_run_async):
            from apps.whatsapp.services.webhook_handler import handle_incoming_message

            t1 = threading.Thread(
                target=handle_incoming_message,
                args=("msg1", "+263100000001", "5 bread"),
            )
            t2 = threading.Thread(
                target=handle_incoming_message,
                args=("msg2", "+263100000001", "3 coke"),
            )
            t1.start()
            time.sleep(0.01)  # ensure t1 acquires lock first
            t2.start()
            t1.join()
            t2.join()

        # msg0 must fully complete before msg1 starts
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0], "msg0:start")
        self.assertEqual(events[1], "msg0:end")
        self.assertEqual(events[2], "msg1:start")
        self.assertEqual(events[3], "msg1:end")

    def test_different_users_process_in_parallel(self):
        """Messages from different users must not block each other."""
        company2 = Company.objects.create(name="Shop 2", slug="shop-2-ms")
        user2 = User.objects.create_user("testuser_ms2", "ms2@example.com", "password")
        UserProfile.objects.create(
            user=user2,
            company=company2,
            phone_number="+263100000002",
            language="en",
        )

        started = []
        barrier = threading.Barrier(2, timeout=3)

        def fake_run_async(coro):
            coro.close()
            # Extract phone from the calling thread's lock context isn't easy,
            # so we just record that we started and wait at the barrier.
            # If one thread were blocking the other, barrier would time out and raise.
            started.append(threading.current_thread().name)
            barrier.wait()

        with patch("apps.whatsapp.services.webhook_handler.run_async", side_effect=fake_run_async):
            from apps.whatsapp.services.webhook_handler import handle_incoming_message

            t1 = threading.Thread(
                target=handle_incoming_message,
                args=("msg1", "+263100000001", "5 bread"),
                name="thread-user1",
            )
            t2 = threading.Thread(
                target=handle_incoming_message,
                args=("msg2", "+263100000002", "3 coke"),
                name="thread-user2",
            )
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        # barrier.wait() raises BrokenBarrierError if one thread never arrived
        self.assertEqual(len(started), 2)
        self.assertIn("thread-user1", started)
        self.assertIn("thread-user2", started)
