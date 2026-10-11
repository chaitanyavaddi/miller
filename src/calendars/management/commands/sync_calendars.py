from django.core.management.base import BaseCommand

from calendars.models import CalendarConnection
from calendars.services import CalendarApiError, CalendarAuthError, CalendarService


class Command(BaseCommand):
    help = ("Re-read every connected calendar, pick up new calendars in each account and renew push channels "
            "before they lapse. Run it every 15-30 minutes; safe to rerun.")

    def handle(self, *args, **options):
        synced = failed = lost = 0
        connections = CalendarConnection.objects.filter(status=CalendarConnection.Status.CONNECTED).select_related("user")
        for connection in connections:
            try:
                CalendarService.sync(connection)
                synced += 1
            except CalendarAuthError:
                lost += 1  # marked disconnected: Settings offers a reconnect
            except CalendarApiError as error:
                failed += 1
                self.stderr.write(f"{connection}: {error}")
        self.stdout.write(f"Synced {synced} calendar account(s), {failed} failed, {lost} need reconnecting.")
