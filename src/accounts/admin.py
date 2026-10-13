from django.contrib import admin
from django.contrib.auth.models import Group
from django.contrib.admin.sites import NotRegistered
from django.db.models import Count, DecimalField, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce, Greatest
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html, format_html_join

from accounts.models import User
from staff.base import ReadOnlyAdmin
from todos.models import Todo
from voice.models import VoiceCommand


try:
    admin.site.unregister(Group)   # Miller has no groups: staff is a flag on the account (make_staff)
except NotRegistered:
    pass


def count_of(queryset, **filters):
    return Subquery(queryset.filter(user=OuterRef("pk"), **filters).order_by().values("user").annotate(n=Count("id")).values("n"))


@admin.register(User)
class UserAdmin(ReadOnlyAdmin):
    """Who is using Miller, and how much: their todos and what they said to it, a click away."""
    list_display = ("email", "name", "kind", "created_at", "last_active", "tasks", "open_tasks", "commands", "spent")
    list_filter = ("is_guest", "is_superuser", "show_meetings", "created_at")
    search_fields = ("email", "name")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50
    fields = ("email", "name", "kind", "is_active", "is_superuser", "timezone", "show_meetings", "created_at", "last_active",
              "tasks_link", "voice_link", "recent_todos", "recent_voice")
    readonly_fields = fields

    def get_queryset(self, request):
        last_todo = Todo.objects.filter(user=OuterRef("pk")).order_by("-updated_at").values("updated_at")[:1]
        last_voice = VoiceCommand.objects.filter(user=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
        spent = (VoiceCommand.objects.filter(user=OuterRef("pk")).order_by().values("user")
                 .annotate(s=Sum(F("llm_cost") + F("stt_cost"), output_field=DecimalField(max_digits=12, decimal_places=6))).values("s"))
        return super().get_queryset(request).annotate(
            _tasks=Coalesce(count_of(Todo.objects, kind="task", deleted_at__isnull=True), 0),
            _open=Coalesce(count_of(Todo.objects, kind="task", closed_at__isnull=True, deleted_at__isnull=True), 0),
            _commands=Coalesce(count_of(VoiceCommand.objects), 0),
            _spent=Subquery(spent),
            _last=Greatest(Coalesce(Subquery(last_todo), F("created_at")), Coalesce(Subquery(last_voice), F("created_at"))),
        )

    @admin.display(description="Kind", ordering="is_guest")
    def kind(self, user):
        return "Guest" if user.is_guest else "Staff" if user.is_superuser else "Member"

    @admin.display(description="Last active", ordering="_last")
    def last_active(self, user):
        return date_format(timezone.localtime(getattr(user, "_last", None) or user.created_at), "DATETIME_FORMAT")

    @admin.display(description="Todos", ordering="_tasks")
    def tasks(self, user):
        return format_html('<a href="{}?user__id__exact={}">{}</a>', reverse("admin:todos_todo_changelist"), user.pk, user._tasks)

    @admin.display(description="Open", ordering="_open")
    def open_tasks(self, user):
        return user._open

    @admin.display(description="Voice", ordering="_commands")
    def commands(self, user):
        return format_html('<a href="{}?user__id__exact={}">{}</a>', reverse("admin:voice_voicecommand_changelist"), user.pk, user._commands)

    @admin.display(description="Voice spend", ordering="_spent")
    def spent(self, user):
        return f"${user._spent:.4f}" if user._spent else "—"

    @admin.display(description="Todos")
    def tasks_link(self, user):
        return format_html('<a href="{}?user__id__exact={}">All of their todos</a>', reverse("admin:todos_todo_changelist"), user.pk)

    @admin.display(description="Voice")
    def voice_link(self, user):
        return format_html('<a href="{}?user__id__exact={}">All of what they said</a>', reverse("admin:voice_voicecommand_changelist"), user.pk)

    @admin.display(description="Latest todos")
    def recent_todos(self, user):
        rows = Todo.objects.filter(user=user, kind="task", deleted_at__isnull=True).order_by("-created_at")[:15]
        return format_html_join("", "<div>{} <span style='color:#888'>{}</span></div>",
                                ((t.title, "done" if t.closed_at else "open") for t in rows)) or "—"

    @admin.display(description="Latest things said")
    def recent_voice(self, user):
        rows = VoiceCommand.objects.filter(user=user).exclude(transcript="").order_by("-created_at")[:10]
        return format_html_join("", "<div>“{}” <span style='color:#888'>{} · {}</span></div>",
                                ((c.transcript, c.outcome, c.created_at.strftime("%b %d %H:%M")) for c in rows)) or "—"
