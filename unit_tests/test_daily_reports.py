"""Unit tests for daily report card generation and comparison context.

These tests do not require an API key or database — they test pure logic
and image generation in isolation.

Run with:
    python manage.py test unit_tests.test_daily_reports
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.whatsapp.services.business_reports import BusinessSnapshot, build_comparison_context
from apps.whatsapp.services.report_card import generate_stat_card


def _make_snapshot(revenue=Decimal("100.00"), sales_count=5, report_date=None, currency_revenues=None):
    return BusinessSnapshot(
        company_id=1,
        report_date=report_date or datetime.date(2026, 5, 25),
        currency="USD",
        sales_count=sales_count,
        items_sold=10,
        revenue=revenue,
        currency_revenues=currency_revenues or {"USD": revenue},
        cost=Decimal("50.00"),
        gross_profit=revenue - Decimal("50.00"),
        top_products=[("Coca Cola", 5), ("Bread", 3)],
        low_stock_items=[],
        out_of_stock_items=[],
    )


class ReportCardGenerationTest(TestCase):

    def test_returns_png_bytes(self):
        snapshot = _make_snapshot()
        comparison = {
            "delta": Decimal("20.00"),
            "yesterday_revenue": Decimal("80.00"),
            "is_best_day_this_week": True,
            "week_revenues": [Decimal("80.00"), Decimal("100.00")],
        }
        result = generate_stat_card(snapshot, comparison, shop_name="Test Shop")
        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"\x89PNG"), "Result should be a valid PNG")
        self.assertGreater(len(result), 1000, "PNG should be a reasonable size")

    def test_no_top_products(self):
        snapshot = BusinessSnapshot(
            company_id=1,
            report_date=datetime.date(2026, 5, 25),
            currency="USD",
            sales_count=2,
            items_sold=2,
            revenue=Decimal("10.00"),
            currency_revenues={"USD": Decimal("10.00")},
            cost=Decimal("5.00"),
            gross_profit=Decimal("5.00"),
            top_products=[],
            low_stock_items=[],
            out_of_stock_items=[],
        )
        comparison = {
            "delta": Decimal("0.00"),
            "yesterday_revenue": Decimal("10.00"),
            "is_best_day_this_week": False,
            "week_revenues": [Decimal("10.00")],
        }
        result = generate_stat_card(snapshot, comparison)
        self.assertTrue(result.startswith(b"\x89PNG"))

    def test_negative_delta(self):
        snapshot = _make_snapshot(revenue=Decimal("60.00"))
        comparison = {
            "delta": Decimal("-20.00"),
            "yesterday_revenue": Decimal("80.00"),
            "is_best_day_this_week": False,
            "week_revenues": [Decimal("80.00"), Decimal("60.00")],
        }
        result = generate_stat_card(snapshot, comparison)
        self.assertTrue(result.startswith(b"\x89PNG"))


class ComparisonContextTest(TestCase):

    def _make_company(self, name="Test Shop"):
        company = MagicMock()
        company.id = 1
        company.name = name
        company.currency = "USD"
        return company

    @patch("apps.whatsapp.services.business_reports.build_business_snapshot")
    def test_positive_delta(self, mock_build):
        today = datetime.date(2026, 5, 25)  # Monday
        # build_business_snapshot called for yesterday (Sunday) and today
        mock_build.side_effect = [
            _make_snapshot(revenue=Decimal("80.00"), report_date=today),   # today (appended)
            _make_snapshot(revenue=Decimal("80.00"), report_date=today - datetime.timedelta(days=1)),  # yesterday
        ]
        company = self._make_company()
        result = build_comparison_context(company, report_date=today)
        self.assertIn("delta", result)
        self.assertIn("yesterday_revenue", result)
        self.assertIn("is_best_day_this_week", result)

    @patch("apps.whatsapp.services.business_reports.build_business_snapshot")
    def test_is_best_day_this_week_true_when_highest(self, mock_build):
        today = datetime.date(2026, 5, 27)  # Wednesday
        monday = datetime.date(2026, 5, 25)
        tuesday = datetime.date(2026, 5, 26)

        # Calls: yesterday, monday, tuesday, today
        mock_build.side_effect = [
            _make_snapshot(revenue=Decimal("50.00"), report_date=tuesday),   # yesterday
            _make_snapshot(revenue=Decimal("40.00"), report_date=monday),    # Mon loop
            _make_snapshot(revenue=Decimal("50.00"), report_date=tuesday),   # Tue loop
            _make_snapshot(revenue=Decimal("100.00"), report_date=today),    # today appended
        ]
        company = self._make_company()
        result = build_comparison_context(company, report_date=today)
        self.assertTrue(result["is_best_day_this_week"])
        self.assertEqual(result["delta"], Decimal("50.00"))

    @patch("apps.whatsapp.services.business_reports.build_business_snapshot")
    def test_is_best_day_false_when_not_highest(self, mock_build):
        today = datetime.date(2026, 5, 27)  # Wednesday
        monday = datetime.date(2026, 5, 25)
        tuesday = datetime.date(2026, 5, 26)

        mock_build.side_effect = [
            _make_snapshot(revenue=Decimal("120.00"), report_date=tuesday),  # yesterday
            _make_snapshot(revenue=Decimal("80.00"), report_date=monday),    # Mon loop
            _make_snapshot(revenue=Decimal("120.00"), report_date=tuesday),  # Tue loop
            _make_snapshot(revenue=Decimal("60.00"), report_date=today),     # today appended
        ]
        company = self._make_company()
        result = build_comparison_context(company, report_date=today)
        self.assertFalse(result["is_best_day_this_week"])
        self.assertEqual(result["delta"], Decimal("-60.00"))
