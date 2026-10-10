from django.utils import timezone


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
