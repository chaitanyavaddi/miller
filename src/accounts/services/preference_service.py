from accounts.models import User


class PreferenceService:
    @staticmethod
    def set_timezone(user: User, timezone: str) -> None:
        user.timezone = timezone
        user.save(update_fields=["timezone", "updated_at"])

    @staticmethod
    def set_show_meetings(user: User, show: bool) -> None:
        user.show_meetings = show
        user.save(update_fields=["show_meetings", "updated_at"])
