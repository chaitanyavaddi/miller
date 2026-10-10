from django.conf import settings

from accounts.schemas import zone_names
from calendars.models import CalendarConnection
from calendars.services import CalendarService


class SettingsService:
    """Read model for the settings modal: the user's zone, and their calendars once meetings are on."""

    @staticmethod
    def build(user, notice: str = "") -> dict:
        providers = [
            (value, label) for value, label in CalendarConnection.Provider.choices if CalendarService.can_connect(value)
        ]
        return {
            "user":         user,
            "zones":        zone_names(),
            "current_zone": user.timezone or settings.TIME_ZONE,
            "connections":  list(CalendarService.connections_for(user)),
            "suggested":    CalendarService.suggested_email(user) if CalendarService.can_connect(CalendarConnection.Provider.GOOGLE) else "",
            "providers":    providers,
            "notice":       notice,
        }
