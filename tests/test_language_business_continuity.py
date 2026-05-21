"""
Test that language switching maintains business data continuity.

Verifies that when users switch between English and Shona, the bot:
- Maintains the same business context (same company, sales records, reports)
- Does not create duplicate companies or separate business accounts
- Processes sales and reports using the same language-agnostic data
"""

import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from decimal import Decimal

from apps.catalog.models import Product
from apps.core.models import Company, UserProfile, WaitlistEntry
from apps.sales.models import Sale, SaleItem
from apps.whatsapp.services.webhook_handler import (
    _extract_phone_number,
    handle_incoming_message,
)
from apps.whatsapp.views import _lookup_sender, SenderStatus
from services.whatsapp.intent_parser import IntentParser


class LanguageBusinessContinuityTestCase(TestCase):
    """Test language continuity with business data."""

    def setUp(self):
        """Create test company, user, and products."""
        self.company = Company.objects.create(
            name="Test Shop",
            slug="test-shop-lang-continuity",
            currency="ZWL"
        )
        
        user = User.objects.create_user(username="testlang123")
        self.user_profile = UserProfile.objects.create(
            user=user,
            company=self.company,
            phone_number="+27812345678",
            language="en",  # Start in English
            role=UserProfile.Role.OWNER,
        )
        
        # Create some products
        self.product_bread = Product.objects.create(
            company=self.company,
            name="Bread",
            cost=Decimal("50.00"),
        )
        self.product_coke = Product.objects.create(
            company=self.company,
            name="Coke",
            cost=Decimal("30.00"),
        )
        
        # Create a sale in English
        self.sale_en = Sale.objects.create(
            company=self.company,
            seller=self.user_profile,
            currency="ZWL",
            total_amount=Decimal("100.00"),
        )
        SaleItem.objects.create(
            sale=self.sale_en,
            product=self.product_bread,
            quantity=2,
            unit_price=Decimal("50.00"),
            subtotal=Decimal("100.00"),
        )

    def test_phone_number_lookup_is_language_independent(self):
        """Phone number lookup should return same profile regardless of language."""
        phone = "+27812345678"
        
        # Lookup should work
        status, profile, _ = _lookup_sender(phone)
        self.assertEqual(status, SenderStatus.KNOWN_USER)
        self.assertEqual(profile.id, self.user_profile.id)
        self.assertEqual(profile.company.id, self.company.id)
        
        # Change language
        self.user_profile.language = "sn"
        self.user_profile.save()
        
        # Lookup should still return same profile
        status, profile, _ = _lookup_sender(phone)
        self.assertEqual(status, SenderStatus.KNOWN_USER)
        self.assertEqual(profile.id, self.user_profile.id)
        self.assertEqual(profile.company.id, self.company.id)

    def test_language_change_does_not_create_duplicate_user_profile(self):
        """Language changes should not create new UserProfile records."""
        phone = "+27812345678"
        initial_profile_count = UserProfile.objects.filter(phone_number=phone).count()
        self.assertEqual(initial_profile_count, 1)
        
        # Change language multiple times
        self.user_profile.language = "sn"
        self.user_profile.save()
        
        self.user_profile.language = "en"
        self.user_profile.save()
        
        self.user_profile.language = "sn"
        self.user_profile.save()
        
        # Should still have only 1 profile
        final_profile_count = UserProfile.objects.filter(phone_number=phone).count()
        self.assertEqual(final_profile_count, 1)

    def test_language_change_does_not_create_duplicate_company(self):
        """Language changes should not create new Company records."""
        initial_company_count = Company.objects.filter(slug=self.company.slug).count()
        self.assertEqual(initial_company_count, 1)
        company_id = self.company.id
        
        # Change language multiple times
        for _ in range(5):
            self.user_profile.language = "sn" if self.user_profile.language == "en" else "en"
            self.user_profile.save()
        
        # Should still have only 1 company with same ID
        final_company_count = Company.objects.filter(slug=self.company.slug).count()
        self.assertEqual(final_company_count, 1)
        
        # Company ID should not have changed
        refreshed_profile = UserProfile.objects.get(id=self.user_profile.id)
        self.assertEqual(refreshed_profile.company.id, company_id)

    def test_sales_accessible_regardless_of_language(self):
        """Sales records should be accessible regardless of user language."""
        initial_sales = Sale.objects.filter(company=self.company).count()
        self.assertEqual(initial_sales, 1)
        
        # Check sale exists when user language is English
        self.user_profile.language = "en"
        self.user_profile.save()
        sales_en = Sale.objects.filter(company=self.company)
        self.assertEqual(sales_en.count(), 1)
        
        # Switch to Shona
        self.user_profile.language = "sn"
        self.user_profile.save()
        
        # Same sale should be accessible
        sales_sn = Sale.objects.filter(company=self.company)
        self.assertEqual(sales_sn.count(), 1)
        self.assertEqual(sales_sn.first().id, self.sale_en.id)

    def test_parser_understands_multilingual_intents(self):
        """Parser should correctly classify intents in both languages."""
        parser = IntentParser()
        
        # English phrases
        en_phrases = [
            ("what sold today", "report.daily_summary"),
            ("profit today", "finance.profit_query"),
            ("close at 8 PM", "shop.closing"),
            ("how many drinks left", "inventory.update"),
        ]
        
        for phrase, expected_intent in en_phrases:
            result = parser.parse(phrase)
            self.assertEqual(result.intent_id, expected_intent, f"Failed for English: {phrase}")
        
        # Shona phrases
        sn_phrases = [
            ("zvinhu zvafamba sei nhasi", "report.daily_summary"),
            ("mari yaita sei today", "finance.profit_query"),
            ("nhasi tine nguva ipi yekuvhara", "shop.closing"),
            ("how many drinks left", "inventory.update"),
        ]
        
        for phrase, expected_intent in sn_phrases:
            result = parser.parse(phrase)
            self.assertEqual(result.intent_id, expected_intent, f"Failed for Shona: {phrase}")

    def test_business_context_maintained_across_message_handlers(self):
        """Business context (company_id) should be maintained in handlers regardless of language."""
        phone = _extract_phone_number("+27812345678")
        
        # Simulate user sending message in English
        self.user_profile.language = "en"
        self.user_profile.save()
        
        status1, profile1, _ = _lookup_sender(phone)
        company_id1 = profile1.company.id if profile1 else None
        
        # Simulate user switching to Shona and sending message
        self.user_profile.language = "sn"
        self.user_profile.save()
        
        status2, profile2, _ = _lookup_sender(phone)
        company_id2 = profile2.company.id if profile2 else None
        
        # Company context should be identical
        self.assertEqual(company_id1, company_id2)
        self.assertEqual(company_id1, self.company.id)

    def test_multiple_users_same_company_language_independence(self):
        """Multiple users in same company should work with different languages."""
        # Create assistant with Shona language
        user2 = User.objects.create_user(username="assistant123")
        assistant_profile = UserProfile.objects.create(
            user=user2,
            company=self.company,
            phone_number="+27887654321",
            language="sn",  # Assistant prefers Shona
            role=UserProfile.Role.ASSISTANT,
        )
        
        # Owner prefers English
        self.user_profile.language = "en"
        self.user_profile.save()
        
        # Both should see same company
        owner_status, owner_prof, _ = _lookup_sender("+27812345678")
        asst_status, asst_prof, _ = _lookup_sender("+27887654321")
        
        self.assertEqual(owner_prof.company.id, self.company.id)
        self.assertEqual(asst_prof.company.id, self.company.id)
        
        # Both should have access to same sales
        owner_sales = Sale.objects.filter(company=owner_prof.company)
        asst_sales = Sale.objects.filter(company=asst_prof.company)
        
        self.assertEqual(owner_sales.count(), asst_sales.count())
        self.assertTrue(owner_sales.exists())

    def test_language_preference_persists_correctly(self):
        """Language preference should be persisted and retrieved correctly."""
        # Test English
        self.user_profile.language = "en"
        self.user_profile.save()
        
        refreshed = UserProfile.objects.get(id=self.user_profile.id)
        self.assertEqual(refreshed.language, "en")
        
        # Test Shona
        self.user_profile.language = "sn"
        self.user_profile.save()
        
        refreshed = UserProfile.objects.get(id=self.user_profile.id)
        self.assertEqual(refreshed.language, "sn")
        
        # Refresh via phone lookup
        status, profile, _ = _lookup_sender("+27812345678")
        self.assertEqual(profile.language, "sn")
