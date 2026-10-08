from dataclasses import dataclass, field

from django.conf import settings
from django.contrib import messages


@dataclass
class CommentCreateSchema:
    body: str
    files: list = field(default_factory=list)

    @classmethod
    def from_post(cls, request) -> "CommentCreateSchema":
        obj = cls(body=request.POST.get("body", "").strip(), files=request.FILES.getlist("files"))
        obj._request = request
        return obj

    def is_valid_content(self) -> bool:
        if not self.body and not self.files:
            messages.error(self._request, "Write something or attach a file")
            return False
        return True

    def is_valid_files(self) -> bool:
        ok = True
        limit_mb = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        for upload in self.files:
            if upload.size > settings.MAX_UPLOAD_BYTES:
                messages.error(self._request, f'"{upload.name}" is over {limit_mb} MB')
                ok = False
        return ok

    def is_valid(self) -> bool:
        return all([self.is_valid_content(), self.is_valid_files()])
