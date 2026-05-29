"""Backfill Company.first_message_date for companies where it is null.

Sets it to the date of the earliest inbound WhatsAppMessage for the company,
or the company's created_at date if no inbound messages exist.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Company
from apps.whatsapp.models import WhatsAppMessage


class Command(BaseCommand):
    help = "Backfill first_message_date for companies where it is null"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be updated without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        companies = Company.objects.filter(first_message_date__isnull=True)
        updated = 0
        skipped = 0

        for company in companies:
            earliest_inbound = (
                WhatsAppMessage.objects.filter(
                    company=company,
                    direction=WhatsAppMessage.Direction.INBOUND,
                )
                .order_by("timestamp")
                .values_list("timestamp", flat=True)
                .first()
            )

            if earliest_inbound:
                date = timezone.localtime(earliest_inbound).date()
                source = "earliest inbound message"
            else:
                date = company.created_at.date()
                source = "company created_at (no inbound messages)"

            self.stdout.write(
                f"{'[DRY RUN] ' if dry_run else ''}Company: {company.name} — "
                f"setting first_message_date={date} (from {source})"
            )

            if not dry_run:
                Company.objects.filter(pk=company.pk).update(first_message_date=date)
            updated += 1

        if skipped:
            self.stdout.write(f"Skipped: {skipped}")
        self.stdout.write(
            f"{'Would update' if dry_run else 'Updated'}: {updated} companies"
        )
