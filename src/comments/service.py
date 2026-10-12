from django.db import transaction
from django.db.models import Q

from comments.models import Attachment, Comment


class CommentService:
    @staticmethod
    def for_todo(todo_id: int) -> list[Comment]:
        return list(Comment.objects.filter(todo_id=todo_id).select_related("recording").prefetch_related("attachments"))

    @staticmethod
    def todo_ids_matching(user, query: str) -> set[int]:
        matches = Comment.objects.filter(todo__user=user).filter(
            Q(body__icontains=query) | Q(attachments__name__icontains=query)
        )
        return set(matches.values_list("todo_id", flat=True))

    @staticmethod
    def note(todo, body: str, recording=None) -> Comment:
        """A line Miller writes into a thread itself, such as where a todo came from (or the recording
        whose transcript opens under it)."""
        return Comment.objects.create(todo=todo, body=body, automatic=True, recording=recording)

    @staticmethod
    @transaction.atomic
    def add(todo, data) -> Comment:
        comment = Comment.objects.create(todo=todo, body=data.body)
        for upload in data.files:
            Attachment.objects.create(
                comment=comment,
                file=upload,
                name=upload.name[:255],
                size=upload.size,
                content_type=(upload.content_type or "")[:100],
            )
        return comment
