from datetime import timedelta

from django.utils import timezone

REFRESH_EVERY = timedelta(days=1)
REFRESHED = "kept_signed_in_at"   # in the session: when its expiry was last moved on


class TimezoneMiddleware:
    """Counts the request's days and times in the user's zone, once they have one."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.timezone:
            timezone.activate(user.tzinfo)
        else:
            timezone.deactivate()
        return self.get_response(request)


class KeepSignedInMiddleware:
    """Someone who keeps using Miller stays signed in. Saving the session sets its expiry (and the cookie's) to
    SESSION_COOKIE_AGE from now; this saves it at most once a day, by stamping when it last did (not every request,
    which would write the session each time)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        session = getattr(request, "session", None)
        user = getattr(request, "user", None)
        if session is not None and user is not None and user.is_authenticated:
            now = timezone.now()
            stamped = session.get(REFRESHED, 0)
            if now.timestamp() - stamped > REFRESH_EVERY.total_seconds():
                session[REFRESHED] = int(now.timestamp())
        return response
