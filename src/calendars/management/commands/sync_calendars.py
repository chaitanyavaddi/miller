from django.core.management.base import BaseCommand

from calendars.models import CalendarConnection
from calendars.services import CalendarService, RecallError


class Command(BaseCommand):
    help = "Pull every connected calendar's events from Recall. A fallback for missed webhooks; safe to run on a schedule."

    def handle(self, *args, **options):
        failed = 0
        connections = CalendarConnection.objects.exclude(status=CalendarConnection.Status.DISCONNECTED).select_related("user")
        for connection in connections:
            try:
                CalendarService.sync(connection)
            except RecallError as error:
                failed += 1
                self.stderr.write(f"{connection}: {error}")
        self.stdout.write(f"Synced {connections.count() - failed} calendar(s), {failed} failed.")
