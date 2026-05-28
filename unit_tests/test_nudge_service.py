"""Unit tests for the GTM drip nudge service."""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth.models import User
from django.test import TransactionTestCase
from django.utils import timezone

from apps.core.models import Company, UserProfile
from apps.whatsapp.services.nudge_service import (
    _compute_streak,
    _eligible_ctas,
    _in_send_window,
    build_shop_context,
    maybe_send_nudges,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_company(name="Test Shop", first_message_date=None, last_nudge_date=None, nudge_stage=0):
    company = Company.objects.create(
        name=name,
        slug=name.lower().replace(" ", "-"),
        active=True,
        first_message_date=first_message_date,
        last_nudge_date=last_nudge_date,
        nudge_stage=nudge_stage,
    )
    return company


def _make_user_profile(company, phone, opted_out=False):
    user = User.objects.create_user(username=phone, password="x")
    profile = UserProfile.objects.create(
        user=user, company=company, phone_number=phone,
        role=UserProfile.Role.OWNER, nudge_opt_out=opted_out,
    )
    return profile


class StreakCalculationTest(TransactionTestCase):

    def setUp(self):
        self.company = _make_company()

    def test_no_sales_streak_is_zero(self):
        self.assertEqual(_compute_streak(self.company), 0)

    def test_streak_with_consecutive_days(self):
        from apps.sales.models import Sale
        today = timezone.localdate()
        for i in range(3):
            Sale.objects.create(
                company=self.company,
                sale_timestamp=timezone.now() - timedelta(days=i),
                total_amount=10,
                status="confirmed",
            )
        self.assertEqual(_compute_streak(self.company), 3)

    def test_streak_resets_on_gap(self):
        from apps.sales.models import Sale
        today = timezone.localdate()
        # Sale today and 2 days ago — gap on yesterday
        Sale.objects.create(company=self.company, sale_timestamp=timezone.now(), total_amount=5, status="confirmed")
        Sale.objects.create(company=self.company, sale_timestamp=timezone.now() - timedelta(days=2), total_amount=5, status="confirmed")
        self.assertEqual(_compute_streak(self.company), 1)


class EligibleCtasTest(TransactionTestCase):

    def test_onboarding_ctas_within_window(self):
        context = {"days_since_onboarding": 3, "streak": 0, "features_used": [], "nudge_stage": 0}
        keys = [c["key"] for c in _eligible_ctas(context)]
        self.assertIn("onboarding_first_sale", keys)
        self.assertIn("onboarding_shona_tip", keys)

    def test_onboarding_ctas_excluded_after_window(self):
        context = {"days_since_onboarding": 20, "streak": 0, "features_used": [], "nudge_stage": 0}
        keys = [c["key"] for c in _eligible_ctas(context)]
        self.assertNotIn("onboarding_first_sale", keys)

    def test_streak_cta_only_when_streak_gte_2(self):
        ctx_no_streak = {"days_since_onboarding": 5, "streak": 1, "features_used": [], "nudge_stage": 0}
        ctx_with_streak = {"days_since_onboarding": 5, "streak": 3, "features_used": [], "nudge_stage": 0}
        self.assertNotIn("retention_streak", [c["key"] for c in _eligible_ctas(ctx_no_streak)])
        self.assertIn("retention_streak", [c["key"] for c in _eligible_ctas(ctx_with_streak)])

    def test_reports_cta_excluded_if_feature_used(self):
        context = {"days_since_onboarding": 5, "streak": 0, "features_used": ["reports"], "nudge_stage": 0}
        keys = [c["key"] for c in _eligible_ctas(context)]
        self.assertNotIn("discovery_weekly_report", keys)

    def test_onboarding_reports_cta_is_button_type(self):
        context = {"days_since_onboarding": 3, "streak": 0, "features_used": [], "nudge_stage": 0}
        ctas = _eligible_ctas(context)
        reports_cta = next((c for c in ctas if c["key"] == "onboarding_reports"), None)
        self.assertIsNotNone(reports_cta)
        self.assertEqual(reports_cta["type"], "button")

    def test_all_other_ctas_are_text_type(self):
        context = {"days_since_onboarding": 3, "streak": 3, "features_used": [], "nudge_stage": 0}
        ctas = _eligible_ctas(context)
        for cta in ctas:
            if cta["key"] != "onboarding_reports":
                self.assertEqual(cta["type"], "text", f"Expected text type for {cta['key']}")


class MaybeSendNudgesTest(TransactionTestCase):

    def setUp(self):
        self.company = _make_company(first_message_date=date.today() - timedelta(days=3))
        self.profile = _make_user_profile(self.company, "+263771000099")

    @patch("apps.whatsapp.services.nudge_service._in_send_window", return_value=False)
    def test_skips_outside_window(self, _mock):
        result = _run(maybe_send_nudges())
        self.assertEqual(result["reason"], "outside_window")
        self.assertEqual(result["sent"], [])

    @patch("apps.whatsapp.services.nudge_service._in_send_window", return_value=True)
    @patch("apps.whatsapp.services.nudge_service._recently_active", return_value=AsyncMock(return_value=True))
    def test_skips_recently_active_shop(self, _mock_active, _mock_window):
        _mock_active.return_value = True
        _mock_active.side_effect = AsyncMock(return_value=True)
        with patch("apps.whatsapp.services.nudge_service._recently_active", new=AsyncMock(return_value=True)):
            result = _run(maybe_send_nudges())
        self.assertIn(self.company.id, result["skipped"])
        self.assertNotIn(self.company.id, result["sent"])

    @patch("apps.whatsapp.services.nudge_service._in_send_window", return_value=True)
    def test_sends_nudge_to_eligible_shop(self, _mock_window):
        async def _run_with_mocks():
            with patch("apps.whatsapp.services.nudge_service._recently_active", new=AsyncMock(return_value=False)), \
                 patch("apps.whatsapp.services.nudge_service.pick_nudge_cta", new=AsyncMock(return_value={"message": "What did you sell today?", "cta_type": "text"})), \
                 patch("apps.whatsapp.services.nudge_service._send_nudge", new=AsyncMock(return_value=None)):
                return await maybe_send_nudges()
        result = _run(_run_with_mocks())
        self.assertIn(self.company.id, result["sent"])
        self.company.refresh_from_db()
        self.assertEqual(self.company.last_nudge_date, date.today())
        self.assertEqual(self.company.nudge_stage, 1)

    @patch("apps.whatsapp.services.nudge_service._in_send_window", return_value=True)
    def test_skips_shop_nudged_today(self, _mock_window):
        self.company.last_nudge_date = date.today()
        self.company.save()
        with patch("apps.whatsapp.services.nudge_service._recently_active", new=AsyncMock(return_value=False)):
            result = _run(maybe_send_nudges())
        self.assertNotIn(self.company.id, result["sent"])

    @patch("apps.whatsapp.services.nudge_service._in_send_window", return_value=True)
    def test_skips_shop_with_all_users_opted_out(self, _mock_window):
        self.profile.nudge_opt_out = True
        self.profile.save()
        with patch("apps.whatsapp.services.nudge_service._recently_active", new=AsyncMock(return_value=False)):
            result = _run(maybe_send_nudges())
        self.assertNotIn(self.company.id, result["sent"])

    @patch("apps.whatsapp.services.nudge_service._in_send_window", return_value=True)
    def test_skips_company_without_first_message_date(self, _mock_window):
        self.company.first_message_date = None
        self.company.save()
        with patch("apps.whatsapp.services.nudge_service._recently_active", new=AsyncMock(return_value=False)):
            result = _run(maybe_send_nudges())
        self.assertNotIn(self.company.id, result["sent"])


class OptOutHandlerTest(TransactionTestCase):
    """Test that 'stop' sets nudge_opt_out on the UserProfile."""

    def setUp(self):
        self.company = _make_company(first_message_date=date.today())
        self.profile = _make_user_profile(self.company, "+263771000088")

    def test_opt_out_flag_set_via_webhook(self):
        from apps.whatsapp.services.webhook_handler import _process_message_async
        with patch("apps.whatsapp.services.webhook_handler.parse_message_unified") as _mock_llm, \
             patch("apps.whatsapp.services.webhook_handler._send_response") as _mock_send:
            _mock_send.return_value = None
            _run(_process_message_async(
                message_id="wamid.test_optout_001",
                sender="+263771000088",
                text="stop",
                user_profile=self.profile,
            ))
            self.profile.refresh_from_db()
            self.assertTrue(self.profile.nudge_opt_out)
            _mock_llm.assert_not_called()
