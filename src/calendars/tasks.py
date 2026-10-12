"""Calendar jobs, run by the django-q2 cluster (qcluster)."""
import time

from calendars.models import CalendarConnection, CalendarSource
from calendars.services import CalendarApiError, CalendarAuthError, CalendarService

RETRY_AFTER = (5, 20)  # seconds between tries when the provider stumbles; the hourly upkeep is the last net


def sync_source(source_id: int) -> bool:
    """A calendar the provider just told us changed: re-read its window into the database. Queued by the
    webhook, which answers at once; retried a couple of times if the provider's API stumbles."""
    source = CalendarSource.objects.filter(pk=source_id, enabled=True).select_related("connection__user").first()
    if source is None or source.connection.status != CalendarConnection.Status.CONNECTED:
        return False
    for wait in (*RETRY_AFTER, None):
        try:
            CalendarService.sync_source(source)
            return True
        except CalendarAuthError:
            return False  # lost access: marked disconnected, Settings offers a reconnect
        except CalendarApiError:
            if wait is None:
                raise
            time.sleep(wait)
    return False


def sync_all() -> int:
    """Hourly upkeep: renew push channels before they lapse, pick up a changed primary calendar, catch a
    missed push. The pushes themselves are what keep meetings current."""
    synced = 0
    for connection in CalendarConnection.objects.filter(status=CalendarConnection.Status.CONNECTED).select_related("user"):
        try:
            CalendarService.sync(connection)
            synced += 1
        except (CalendarAuthError, CalendarApiError):
            continue  # marked disconnected (auth), or tried again next hour
    return synced
