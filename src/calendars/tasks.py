"""Calendar jobs, run by the django-q2 cluster (qcluster)."""
from calendars.models import CalendarConnection
from calendars.services import CalendarApiError, CalendarAuthError, CalendarService


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
