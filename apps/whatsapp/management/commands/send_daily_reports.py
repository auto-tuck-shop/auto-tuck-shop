import asyncio

import sentry_sdk
from django.core.management.base import BaseCommand

from apps.whatsapp.services.business_reports import maybe_send_daily_notifications


class Command(BaseCommand):
    help = "Send daily closing prompts and end-of-day summaries to active shops"

    def handle(self, *args, **options):
        monitor_config = {
            "schedule": {"type": "crontab", "value": "*/15 * * * *"},
            "timezone": "Africa/Harare",
            "checkin_margin": 2,
            "max_runtime": 5,
            "failure_issue_threshold": 1,
        }
        with sentry_sdk.monitor(monitor_slug="send-daily-reports", monitor_config=monitor_config):
            result = asyncio.run(maybe_send_daily_notifications())
            self.stdout.write(
                f"Prompts sent: {result['prompt']}  |  Summaries sent: {result['summary']}"
            )
