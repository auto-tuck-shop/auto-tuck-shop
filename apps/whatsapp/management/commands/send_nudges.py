import asyncio

from django.core.management.base import BaseCommand

from apps.whatsapp.services.nudge_service import maybe_send_nudges


class Command(BaseCommand):
    help = "Send daily nudge messages to shops that have not recorded sales recently"

    def handle(self, *args, **options):
        result = asyncio.run(maybe_send_nudges())
        reason = result.get("reason", "")
        if reason == "outside_window":
            self.stdout.write("Outside nudge send window — nothing sent")
            return
        self.stdout.write(
            f"Nudges sent: {result['sent']}, skipped: {result['skipped']}"
        )
