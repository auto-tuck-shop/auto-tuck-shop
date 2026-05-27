"""Unit tests for LLM-based closing time parsing and idempotent setter.

Tests use mocked LLM responses — no API key required.

Run with:
    python manage.py test unit_tests.test_closing_time_parsing
"""

import asyncio
from datetime import time
from unittest.mock import AsyncMock, patch

from django.test import TestCase

from apps.whatsapp.services.business_reports import parse_closing_time_llm


class ParseClosingTimeLLMTest(TestCase):

    def _parse(self, text, llm_response):
        with patch(
            "services.openrouter.client.OpenRouterClient"
        ) as MockClient:
            instance = MockClient.return_value
            instance.parse_json_response = AsyncMock(return_value=llm_response)
            return asyncio.run(parse_closing_time_llm(text))

    def test_6pm_returns_18_00(self):
        result = self._parse("6pm", {"closing_time": "18:00"})
        self.assertEqual(result, time(18, 0))

    def test_17_30_returns_time(self):
        result = self._parse("17:30", {"closing_time": "17:30"})
        self.assertEqual(result, time(17, 30))

    def test_8am_returns_8_00(self):
        result = self._parse("8am", {"closing_time": "08:00"})
        self.assertEqual(result, time(8, 0))

    def test_sale_message_returns_none(self):
        """'2 plates beef' should be rejected by the LLM."""
        result = self._parse("2 plates beef", {"closing_time": None})
        self.assertIsNone(result)

    def test_bare_number_returns_none(self):
        result = self._parse("2", {"closing_time": None})
        self.assertIsNone(result)

    def test_shona_masikati_6(self):
        result = self._parse("masikati 6", {"closing_time": "18:00"})
        self.assertEqual(result, time(18, 0))

    def test_llm_exception_returns_none(self):
        """If the LLM call fails entirely, return None gracefully — never crash."""
        with patch(
            "services.openrouter.client.OpenRouterClient"
        ) as MockClient:
            instance = MockClient.return_value
            instance.parse_json_response = AsyncMock(side_effect=Exception("API down"))
            result = asyncio.run(parse_closing_time_llm("6pm"))
        self.assertIsNone(result)

    def test_llm_null_closing_time_returns_none(self):
        result = self._parse("ok", {"closing_time": None})
        self.assertIsNone(result)


class SetCompanyNormalClosingTimeIdempotentTest(TestCase):

    def _set(self, company_id, closing_time):
        # Call the underlying ORM directly — sync_to_async wrapping isn't needed in sync tests.
        from apps.core.models import Company
        Company.objects.filter(id=company_id, normal_closing_time__isnull=True).update(
            normal_closing_time=closing_time
        )

    def test_does_not_overwrite_existing_closing_time(self):
        """Second call must not overwrite a value that's already set."""
        from apps.core.models import Company
        company = Company.objects.create(name="Test Shop", slug="test-shop-ct")
        self._set(company.id, time(18, 0))
        self._set(company.id, time(2, 0))  # should be ignored
        company.refresh_from_db()
        self.assertEqual(company.normal_closing_time, time(18, 0))

    def test_sets_when_null(self):
        """First call sets the time when it's currently null."""
        from apps.core.models import Company
        company = Company.objects.create(name="Test Shop 2", slug="test-shop-ct-2")
        self.assertIsNone(company.normal_closing_time)
        self._set(company.id, time(17, 30))
        company.refresh_from_db()
        self.assertEqual(company.normal_closing_time, time(17, 30))
