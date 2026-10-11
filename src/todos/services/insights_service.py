from datetime import datetime

from django.utils import timezone


class InsightsService:
    @staticmethod
    def days_between(start: datetime, end: datetime) -> int:
        """Calendar days, as the list counts them: added yesterday is one day, whatever the hour."""
        return (timezone.localtime(end).date() - timezone.localtime(start).date()).days
