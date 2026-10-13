from django.contrib import admin

from comments.models import Comment
from staff.base import ReadOnlyAdmin, ReadOnlyInline
from todos.models import Todo


class StatusFilter(admin.SimpleListFilter):
    title = "status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("open", "Open"), ("done", "Done"), ("deleted", "Deleted")]

    def queryset(self, request, queryset):
        match self.value():
            case "open":
                return queryset.filter(closed_at__isnull=True, deleted_at__isnull=True)
            case "done":
                return queryset.filter(closed_at__isnull=False, deleted_at__isnull=True)
            case "deleted":
                return queryset.filter(deleted_at__isnull=False)
        return queryset


class NoteInline(ReadOnlyInline):
    model = Comment
    fields = ("created_at", "automatic", "body")
    readonly_fields = fields
    ordering = ("created_at", "id")
    verbose_name_plural = "Thread"


@admin.register(Todo)
class TodoAdmin(ReadOnlyAdmin):
    list_display = ("title", "user", "kind", "status", "position", "created_at", "closed_at")
    list_filter = (StatusFilter, "kind", "user__is_guest", "created_at")
    search_fields = ("title", "user__email")
    list_select_related = ("user",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50
    inlines = [NoteInline]

    @admin.display(description="Status")
    def status(self, todo):
        return "Deleted" if todo.deleted_at else "Done" if todo.closed_at else "Open"
