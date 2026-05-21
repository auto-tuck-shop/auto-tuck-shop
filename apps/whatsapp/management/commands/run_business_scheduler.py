import asyncio
import time as time_module

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.whatsapp.services.business_reports import maybe_send_daily_notifications


class Command(BaseCommand):
    help = "Run the daily 5 PM prompt + closing summary scheduler."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one scheduler tick and exit.",
        )

    def handle(self, *args, **options):
        once = options.get("once", False)
        if once:
            result = asyncio.run(maybe_send_daily_notifications())
            self.stdout.write(self.style.SUCCESS(f"Scheduler tick complete: {result}"))
            return

        self.stdout.write(self.style.SUCCESS("Starting daily business scheduler..."))
        try:
            while True:
                result = asyncio.run(maybe_send_daily_notifications())
                if result.get("prompt") or result.get("summary"):
                    self.stdout.write(self.style.SUCCESS(f"Scheduler tick complete: {result}"))
                now = timezone.now()
                sleep_for = max(10, 60 - now.second)
                time_module.sleep(sleep_for)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Scheduler stopped."))
