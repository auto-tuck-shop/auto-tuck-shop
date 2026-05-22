"""Test that language switching maintains business data continuity.

Verifies that when users switch between English and Shona, the bot:
- Maintains the same business context (same company, sales records)
- Does not create duplicate companies or user profiles
"""

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import TestCase

from apps.core.models import Company, UserProfile
from apps.sales.models import Sale
from apps.whatsapp.services.webhook_handler import _extract_phone_number
from apps.whatsapp.views import _lookup_sender, SenderStatus


class LanguageBusinessContinuityTestCase(TestCase):

    def setUp(self):
        self.company = Company.objects.create(
            name="Test Shop",
            slug="test-shop-lang-continuity",
            currency="ZWG",
        )
        user = User.objects.create_user(username="testlang123")
        self.user_profile = UserProfile.objects.create(
            user=user,
            company=self.company,
            phone_number="+27812345678",
            language="en",
            role=UserProfile.Role.OWNER,
        )

    def test_phone_number_lookup_is_language_independent(self):
        phone = "+27812345678"
        status, profile, _ = _lookup_sender(phone)
        self.assertEqual(status, SenderStatus.KNOWN_USER)
        self.assertEqual(profile.id, self.user_profile.id)

        self.user_profile.language = "sn"
        self.user_profile.save()

        status, profile, _ = _lookup_sender(phone)
        self.assertEqual(status, SenderStatus.KNOWN_USER)
        self.assertEqual(profile.id, self.user_profile.id)
        self.assertEqual(profile.company.id, self.company.id)

    def test_language_change_does_not_create_duplicate_user_profile(self):
        phone = "+27812345678"
        for lang in ["sn", "en", "sn"]:
            self.user_profile.language = lang
            self.user_profile.save()
        self.assertEqual(UserProfile.objects.filter(phone_number=phone).count(), 1)

    def test_language_change_does_not_create_duplicate_company(self):
        company_id = self.company.id
        for _ in range(5):
            self.user_profile.language = "sn" if self.user_profile.language == "en" else "en"
            self.user_profile.save()
        self.assertEqual(Company.objects.filter(slug=self.company.slug).count(), 1)
        self.assertEqual(UserProfile.objects.get(id=self.user_profile.id).company.id, company_id)

    def test_business_context_maintained_across_language_switch(self):
        phone = _extract_phone_number("+27812345678")
        for lang in ["en", "sn"]:
            self.user_profile.language = lang
            self.user_profile.save()
            _, profile, _ = _lookup_sender(phone)
            self.assertEqual(profile.company.id, self.company.id)

    def test_multiple_users_same_company_language_independence(self):
        user2 = User.objects.create_user(username="assistant123")
        UserProfile.objects.create(
            user=user2,
            company=self.company,
            phone_number="+27887654321",
            language="sn",
            role=UserProfile.Role.ASSISTANT,
        )
        self.user_profile.language = "en"
        self.user_profile.save()

        _, owner_prof, _ = _lookup_sender("+27812345678")
        _, asst_prof, _ = _lookup_sender("+27887654321")

        self.assertEqual(owner_prof.company.id, self.company.id)
        self.assertEqual(asst_prof.company.id, self.company.id)

    def test_language_preference_persists_correctly(self):
        for lang in ["en", "sn"]:
            self.user_profile.language = lang
            self.user_profile.save()
            refreshed = UserProfile.objects.get(id=self.user_profile.id)
            self.assertEqual(refreshed.language, lang)
        _, profile, _ = _lookup_sender("+27812345678")
        self.assertEqual(profile.language, "sn")
