from dataclasses import dataclass
from functools import cache
from zoneinfo import available_timezones

from django.contrib import messages


@cache
def zone_names() -> list[str]:
    """Region/City zones people pick from, plus UTC; not the legacy aliases."""
    legacy = ("Etc/", "SystemV/", "US/", "Canada/", "Brazil/", "Chile/", "Mexico/", "posix/", "right/")
    return sorted(z for z in available_timezones() if "/" in z and not z.startswith(legacy)) + ["UTC"]


@dataclass
class TimezoneSchema:
    timezone: str
    detected: bool  # sent by the browser on a first visit, not chosen by the user

    @classmethod
    def from_post(cls, request) -> "TimezoneSchema":
        obj = cls(timezone=request.POST.get("timezone", "").strip(), detected=request.POST.get("detected") == "1")
        obj._request = request
        return obj

    @property
    def is_known(self) -> bool:
        return self.timezone in available_timezones()

    def is_valid_timezone(self) -> bool:
        if not self.is_known:
            messages.error(self._request, "Pick a time zone from the list")
            return False
        return True

    def is_valid(self) -> bool:
        return all([self.is_valid_timezone()])
