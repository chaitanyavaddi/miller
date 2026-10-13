import json

from django.contrib import admin

from staff.base import ReadOnlyAdmin, pre, short
from voice.models import VoiceCommand


class SpeedFilter(admin.SimpleListFilter):
    title = "how long it took"
    parameter_name = "speed"

    def lookups(self, request, model_admin):
        return [("2", "Over 2 seconds"), ("4", "Over 4 seconds"), ("8", "Over 8 seconds")]

    def queryset(self, request, queryset):
        return queryset.filter(total_ms__gte=int(self.value()) * 1000) if self.value() else queryset


@admin.register(VoiceCommand)
class VoiceCommandAdmin(ReadOnlyAdmin):
    """Each thing someone said, what Miller heard, what the model made of it and what it did, how long it took
    and what it cost: the place to start when voice does something unexpected."""
    list_display = ("created_at", "user", "outcome", "said", "answer", "seconds", "cost_usd")
    list_filter = ("outcome", SpeedFilter, "user__is_guest", "model", "created_at")
    search_fields = ("transcript", "reply", "user__email")
    list_select_related = ("user",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50
    fieldsets = (
        ("What happened", {"fields": ("user", "created_at", "outcome", "transcript", "reply")}),
        ("What the model asked for", {"fields": ("model", "operations_json", "applied_json", "undone_at")}),
        ("Timing", {"fields": ("audio_seconds", "heard_ms", "decide_ms", "total_ms")}),
        ("Cost", {"fields": ("llm_cost", "stt_cost", "tokens_in", "tokens_out")}),
        ("Conversation", {"fields": ("session",)}),
    )
    readonly_fields = ("user", "created_at", "outcome", "transcript", "reply", "model", "operations_json", "applied_json", "undone_at",
                       "audio_seconds", "heard_ms", "decide_ms", "total_ms", "llm_cost", "stt_cost", "tokens_in", "tokens_out", "session")

    @admin.display(description="Said")
    def said(self, command):
        return short(command.transcript, 70) or "—"

    @admin.display(description="Miller's line")
    def answer(self, command):
        return short(command.reply, 70) or "—"

    @admin.display(description="Took", ordering="total_ms")
    def seconds(self, command):
        return f"{command.total_ms / 1000:.1f}s" if command.total_ms else "—"

    @admin.display(description="Cost", ordering="llm_cost")
    def cost_usd(self, command):
        return f"${command.cost:.4f}"

    @admin.display(description="Operations")
    def operations_json(self, command):
        return pre(json.dumps(command.operations, indent=2, ensure_ascii=False))

    @admin.display(description="Applied")
    def applied_json(self, command):
        return pre(json.dumps(command.applied, indent=2, ensure_ascii=False))
