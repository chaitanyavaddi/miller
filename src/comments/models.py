import os

from django.db import models
from django.urls import reverse


def attachment_upload_to(attachment, filename):
    todo = attachment.comment.todo
    return f"u{todo.user_id}/{todo.id}/{os.path.basename(filename)}"


class Comment(models.Model):
    todo       = models.ForeignKey("todos.Todo", on_delete=models.CASCADE, related_name="comments")
    body       = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "todos_comment"
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.body[:60]


class Attachment(models.Model):
    comment      = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="attachments")
    file         = models.FileField(upload_to=attachment_upload_to, max_length=500)
    name         = models.CharField(max_length=255)
    size         = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "todos_attachment"
        ordering = ["id"]

    def __str__(self):
        return self.name

    @property
    def url(self):
        return reverse("comments:attachment", args=[self.pk])

    @property
    def is_image(self):
        return self.content_type.startswith("image/")
