import asyncio

from django.core.management.base import BaseCommand

from apps.whatsapp.services.business_reports import maybe_send_daily_notifications


class Command(BaseCommand):
    help = "Send daily closing prompts and end-of-day summaries to active shops"

    def handle(self, *args, **options):
        result = asyncio.run(maybe_send_daily_notifications())
        self.stdout.write(
            f"Prompts sent: {result['prompt']}  |  Summaries sent: {result['summary']}"
        )
