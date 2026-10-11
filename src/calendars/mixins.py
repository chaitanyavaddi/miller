from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from accounts.mixins import SignedInRequiredMixin
from calendars.models import CalendarConnection, CalendarSource


class CalendarConnectionPermissionRequiredMixin(SignedInRequiredMixin):
    """Resolves the `pk` URL kwarg to `self.connection` and requires a `calendars.*` permission on it."""
    permission_required = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.connection = get_object_or_404(CalendarConnection, pk=kwargs.get("pk"))
        if self.permission_required and not request.user.has_perm(self.permission_required, self.connection):
            raise PermissionDenied(f"Missing permission: {self.permission_required}")
        return super().dispatch(request, *args, **kwargs)


class CalendarSourcePermissionRequiredMixin(SignedInRequiredMixin):
    """Resolves the `pk` URL kwarg to `self.source` and requires a `calendars.*` permission on it."""
    permission_required = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.source = get_object_or_404(CalendarSource.objects.select_related("connection"), pk=kwargs.get("pk"))
        if self.permission_required and not request.user.has_perm(self.permission_required, self.source):
            raise PermissionDenied(f"Missing permission: {self.permission_required}")
        return super().dispatch(request, *args, **kwargs)
