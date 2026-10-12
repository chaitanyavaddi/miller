from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from accounts.mixins import SignedInRequiredMixin
from recordings.models import Recording


class RecordingPermissionRequiredMixin(SignedInRequiredMixin):
    """Resolves the `pk` URL kwarg to `self.recording` and requires a `recordings.*` permission on it."""
    permission_required = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.recording = get_object_or_404(Recording, pk=kwargs.get("pk"))
        if self.permission_required and not request.user.has_perm(self.permission_required, self.recording):
            raise PermissionDenied(f"Missing permission: {self.permission_required}")
        return super().dispatch(request, *args, **kwargs)
