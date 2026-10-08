from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from accounts.mixins import SignedInRequiredMixin
from comments.models import Attachment


class AttachmentPermissionRequiredMixin(SignedInRequiredMixin):
    """Resolves the `pk` URL kwarg to `self.attachment` and requires a `comments.*` permission on it."""
    permission_required = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.attachment = get_object_or_404(Attachment.objects.select_related("comment__todo"), pk=kwargs.get("pk"))
        if self.permission_required and not request.user.has_perm(self.permission_required, self.attachment):
            raise PermissionDenied(f"Missing permission: {self.permission_required}")
        return super().dispatch(request, *args, **kwargs)
