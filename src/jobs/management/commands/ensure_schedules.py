from django.core.management.base import BaseCommand

from jobs.schedules import ensure


class Command(BaseCommand):
    help = "Write Miller's recurring jobs into django-q2's schedule table. Run on every deploy; safe to rerun."

    def handle(self, *args, **options):
        for name in ensure():
            self.stdout.write(f"Scheduled: {name}")
