from django.http import FileResponse, Http404
from django.views import View

from comments.mixins import AttachmentPermissionRequiredMixin


class AttachmentView(AttachmentPermissionRequiredMixin, View):
    permission_required = "comments.view_attachment"

    def get(self, request, pk):
        attachment = self.attachment
        if not attachment.file:
            raise Http404
        return FileResponse(
            attachment.file.open("rb"),
            as_attachment=not attachment.is_image,
            filename=attachment.name,
            content_type=attachment.content_type or None,
        )
