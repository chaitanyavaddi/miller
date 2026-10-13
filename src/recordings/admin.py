from django.contrib import admin

from recordings.models import Recording, RecordingPart
from staff.base import ReadOnlyAdmin, ReadOnlyInline, pre, short


class PartInline(ReadOnlyInline):
    model = RecordingPart
    fields = ("track", "index", "size", "transcribed", "started_at")   # the words stay out
    readonly_fields = fields


@admin.register(Recording)
class RecordingAdmin(ReadOnlyAdmin):
    """Calls and voice notes: how each got on (state, attempts, errors), never what was said in them."""
    list_display = ("created_at", "user", "kind", "status", "interrupted", "duration", "attempts", "problem")
    list_filter = ("status", "kind", "interrupted", "created_at")
    search_fields = ("user__email", "error")
    list_select_related = ("user",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    exclude = ("transcript",)
    readonly_fields = ("user", "todo", "kind", "status", "interrupted", "last_chunk_at", "ended_at", "duration", "claimed_at", "attempts",
                       "error_text", "transcript_lines", "created_at", "updated_at")
    fields = readonly_fields
    inlines = [PartInline]

    @admin.display(description="Problem")
    def problem(self, recording):
        return short(recording.error, 70) or "—"

    @admin.display(description="Error")
    def error_text(self, recording):
        return pre(recording.error)

    @admin.display(description="Transcript")
    def transcript_lines(self, recording):
        return f"{len(recording.transcript)} lines (not shown)"
