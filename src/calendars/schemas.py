from dataclasses import dataclass


@dataclass
class SourceToggleSchema:
    """One calendar of an account switched on or off in Settings."""
    enabled: bool

    @classmethod
    def from_post(cls, request) -> "SourceToggleSchema":
        obj = cls(enabled=request.POST.get("enabled") == "1")
        obj._request = request
        return obj

    def is_valid(self) -> bool:
        return True
