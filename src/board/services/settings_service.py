from django.conf import settings

from accounts.schemas import zone_names
from calendars.models import CalendarConnection
from calendars.services import CalendarService


# Providers offered in Settings. Outlook works end to end but stays hidden until it's set up and verified.
OFFERED_PROVIDERS = (CalendarConnection.Provider.GOOGLE,)


class SettingsService:
    """Read model for the settings modal: the user's zone and their connected calendars."""

    @staticmethod
    def build(user, notice: str = "") -> dict:
        providers = [
            (value, label) for value, label in CalendarConnection.Provider.choices
            if value in OFFERED_PROVIDERS and CalendarService.can_connect(value)
        ]
        return {
            "user":         user,
            "zones":        zone_names(),
            "current_zone": user.timezone or settings.TIME_ZONE,
            "connections":  list(CalendarService.connections_for(user)),
            "providers":    providers,
            "notice":       notice,
        }
