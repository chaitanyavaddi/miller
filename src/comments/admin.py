from django.contrib import admin

from comments.models import Attachment, Comment
from staff.base import ReadOnlyAdmin, ReadOnlyInline, short


class AttachmentInline(ReadOnlyInline):
    model = Attachment
    fields = ("name", "size", "content_type")   # never the file itself
    readonly_fields = fields


@admin.register(Comment)
class CommentAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "user", "todo", "text", "automatic")
    list_filter = ("automatic", "created_at")
    search_fields = ("body", "todo__title", "todo__user__email")
    list_select_related = ("todo", "todo__user")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    inlines = [AttachmentInline]

    @admin.display(description="Note")
    def text(self, comment):
        return short(comment.body, 90)

    @admin.display(description="User", ordering="todo__user__email")
    def user(self, comment):
        return comment.todo.user
