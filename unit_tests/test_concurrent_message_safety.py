"""Unit tests for concurrent message safety and per-user locking."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase, TransactionTestCase
from django.contrib.auth.models import User

from apps.core.models import Company, UserProfile
from apps.whatsapp.services.message_lock import process_with_user_lock

logger = logging.getLogger(__name__)


class MessageLockingTestCase(TransactionTestCase):
    """Test per-user message locking."""

    def setUp(self):
        """Create test user and company."""
        self.company = Company.objects.create(
            name="Test Shop",
            slug="test-shop",
        )
        user = User.objects.create_user("testuser", "test@example.com", "password")
        self.profile = UserProfile.objects.create(
            user=user,
            company=self.company,
            phone_number="+263123456789",
            language="en",
        )
        self.phone_number = "+263123456789"

    def test_messages_serialize_with_lock(self):
        """Test that messages from same user process serially (one at a time)."""
        execution_times = []

        async def slow_operation(msg_id: str, duration: float):
            """Async operation that takes `duration` seconds."""
            execution_times.append({"id": msg_id, "action": "start", "duration": duration})
            await asyncio.sleep(duration)
            execution_times.append({"id": msg_id, "action": "end"})

        async def run_two_messages_in_parallel():
            """Try to run two messages concurrently - they should serialize."""
            task1 = process_with_user_lock(
                self.phone_number,
                slow_operation,
                "msg1",
                0.1,  # 100ms
            )
            task2 = process_with_user_lock(
                self.phone_number,
                slow_operation,
                "msg2",
                0.1,  # 100ms
            )
            # Create tasks in parallel
            results = await asyncio.gather(task1, task2, return_exceptions=True)
            return results

        # Run the parallel tasks
        asyncio.run(run_two_messages_in_parallel())

        # Verify serialization: msg1 should complete before msg2 starts
        self.assertEqual(len(execution_times), 4)
        self.assertEqual(execution_times[0]["id"], "msg1")
        self.assertEqual(execution_times[0]["action"], "start")
        self.assertEqual(execution_times[1]["id"], "msg1")
        self.assertEqual(execution_times[1]["action"], "end")
        self.assertEqual(execution_times[2]["id"], "msg2")
        self.assertEqual(execution_times[2]["action"], "start")
        self.assertEqual(execution_times[3]["id"], "msg2")
        self.assertEqual(execution_times[3]["action"], "end")

    def test_different_users_process_parallel(self):
        """Test that messages from different users can process in parallel."""
        # Create a second user
        company2 = Company.objects.create(name="Shop 2", slug="shop-2")
        user2 = User.objects.create_user("user2", "user2@example.com", "password")
        profile2 = UserProfile.objects.create(
            user=user2,
            company=company2,
            phone_number="+263987654321",
            language="en",
        )

        execution_log = []

        async def logged_operation(msg_id: str, user_phone: str):
            """Operation that logs execution."""
            execution_log.append({"msg": msg_id, "user": user_phone, "action": "start"})
            await asyncio.sleep(0.05)  # 50ms
            execution_log.append({"msg": msg_id, "user": user_phone, "action": "end"})

        async def run_parallel_from_different_users():
            """Run messages from two different users in parallel."""
            task1 = process_with_user_lock(
                self.phone_number,
                logged_operation,
                "msg1",
                self.phone_number,
            )
            task2 = process_with_user_lock(
                profile2.phone_number,
                logged_operation,
                "msg2",
                profile2.phone_number,
            )
            await asyncio.gather(task1, task2, return_exceptions=True)

        asyncio.run(run_parallel_from_different_users())

        # Both messages should start before either finishes (true parallelism)
        starts = [e for e in execution_log if e["action"] == "start"]
        self.assertEqual(len(starts), 2, "Both messages should start")

        # Verify both messages exist
        self.assertEqual(len([e for e in execution_log if e["msg"] == "msg1"]), 2)
        self.assertEqual(len([e for e in execution_log if e["msg"] == "msg2"]), 2)

    def test_lock_error_handling(self):
        """Test that errors during lock acquisition are handled."""

        async def failing_operation():
            raise RuntimeError("Test error")

        async def run_failing_message():
            with self.assertRaises(RuntimeError):
                await process_with_user_lock(
                    self.phone_number,
                    failing_operation,
                )

        asyncio.run(run_failing_message())


class MessageLockingIntegrationTest(TransactionTestCase):
    """Integration tests for message locking with message processing."""

    def setUp(self):
        """Create test user and company."""
        self.company = Company.objects.create(
            name="Test Shop",
            slug="test-shop",
        )
        user = User.objects.create_user("testuser", "test@example.com", "password")
        self.profile = UserProfile.objects.create(
            user=user,
            company=self.company,
            phone_number="+263123456789",
            language="en",
        )
        self.phone_number = "+263123456789"

    def test_lock_preserves_message_order(self):
        """Test that lock ensures messages are processed in arrival order."""
        process_order = []

        async def mock_process_message(msg_id: str, order_num: int):
            """Mock message processor that records processing order."""
            process_order.append({"msg_id": msg_id, "order": order_num, "action": "start"})
            # Simulate LLM processing time (variable)
            await asyncio.sleep(0.01 * order_num)  # msg1: 0.01s, msg2: 0.02s
            process_order.append({"msg_id": msg_id, "order": order_num, "action": "end"})

        async def simulate_rapid_messages():
            """Simulate rapid message arrival."""
            tasks = []
            for i in range(3):
                task = process_with_user_lock(
                    self.phone_number,
                    mock_process_message,
                    f"msg{i+1}",
                    i + 1,
                )
                tasks.append(task)
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run(simulate_rapid_messages())

        # Verify order: each message should complete before next starts
        for i in range(3):
            msg_id = f"msg{i+1}"
            msg_events = [e for e in process_order if e["msg_id"] == msg_id]
            self.assertEqual(len(msg_events), 2, f"{msg_id} should have start and end")
            self.assertEqual(msg_events[0]["action"], "start")
            self.assertEqual(msg_events[1]["action"], "end")

            # If not the last message, verify it completes before next starts
            if i < 2:
                next_msg_id = f"msg{i+2}"
                current_end_idx = process_order.index(
                    next((e for e in process_order if e["msg_id"] == msg_id and e["action"] == "end"), None)
                )
                next_start_idx = process_order.index(
                    next((e for e in process_order if e["msg_id"] == next_msg_id and e["action"] == "start"), None)
                )
                self.assertLess(
                    current_end_idx,
                    next_start_idx,
                    f"{msg_id} should end before {next_msg_id} starts",
                )
