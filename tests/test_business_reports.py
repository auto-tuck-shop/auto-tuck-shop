import asyncio
from datetime import datetime, time
import uuid

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.catalog.models import Product
from apps.core.models import Company, UserProfile
from apps.inventory.models import InventoryAdjustment
from apps.sales.models import Sale, SaleItem
from apps.whatsapp.services import business_reports
from services.whatsapp.mock_client import MockWhatsAppClient


class TestBusinessReports:
    def _make_company(self):
        suffix = uuid.uuid4().hex[:8]
        company = Company.objects.create(
            name="Auto Tuck Shop",
            slug=f"auto-tuck-{suffix}",
            currency="ZAR",
        )
        user = User.objects.create_user(username=f"owner-{suffix}")
        UserProfile.objects.create(
            user=user,
            company=company,
            role=UserProfile.Role.OWNER,
            phone_number=f"+2782{suffix}01",
            language="en",
        )
        return company

    def test_parse_closing_time_text(self):
        assert business_reports.parse_closing_time_text("6pm") == time(18, 0)
        assert business_reports.parse_closing_time_text("17:30") == time(17, 30)
        assert business_reports.parse_closing_time_text("close at 8") == time(8, 0)

    def test_build_daily_business_summary(self):
        company = self._make_company()

        bread = Product.objects.create(company=company, name="bread", cost="4.00", active=True)
        coke = Product.objects.create(company=company, name="coke", cost="2.00", active=True)

        InventoryAdjustment.objects.create(product=bread, quantity_delta=5, reason=InventoryAdjustment.Reason.INITIAL)
        InventoryAdjustment.objects.create(product=coke, quantity_delta=2, reason=InventoryAdjustment.Reason.INITIAL)

        sale = Sale.objects.create(company=company, status=Sale.Status.CONFIRMED)
        SaleItem.objects.create(sale=sale, product=bread, quantity=2, unit_price="10.00", currency="ZAR")
        SaleItem.objects.create(sale=sale, product=coke, quantity=1, unit_price="5.00", currency="ZAR")
        sale.save()

        snapshot = business_reports.build_business_snapshot(company, report_date=timezone.localdate())
        message = business_reports.format_business_summary(snapshot)

        assert "Sales: 1" in message
        assert "Revenue: R25.00" in message or "Revenue: ZAR 25.00" in message
        assert "Gross profit: R15.00" in message or "Gross profit: ZAR 15.00" in message
        assert "Top products:" in message
        assert "bread: 2" in message
        assert "Low stock:" in message or "Out of stock:" in message

    def test_scheduler_sends_summary_after_closing_time(self, monkeypatch):
        company = self._make_company()
        product = Product.objects.create(company=company, name="bread", cost="4.00", active=True)
        InventoryAdjustment.objects.create(product=product, quantity_delta=5, reason=InventoryAdjustment.Reason.INITIAL)
        sale = Sale.objects.create(company=company, status=Sale.Status.CONFIRMED)
        SaleItem.objects.create(sale=sale, product=product, quantity=1, unit_price="10.00", currency="ZAR")
        sale.save()

        MockWhatsAppClient.reset()
        monkeypatch.setattr(business_reports, "get_whatsapp_client", lambda: MockWhatsAppClient())

        today = timezone.localdate()
        Company.objects.filter(id=company.id).update(
            daily_closing_time=time(18, 0),
            daily_closing_date=today,
            last_closing_prompt_date=today,
            last_summary_date=None,
        )

        result = asyncio.run(
            business_reports.maybe_send_daily_notifications(
                now=timezone.make_aware(datetime.combine(today, time(18, 5)))
            )
        )

        assert company.id in result["summary"]
        assert MockWhatsAppClient.sent_messages
        sent_texts = [m["text"] for m in MockWhatsAppClient.sent_messages]
        assert any("Business summary" in text for text in sent_texts)
