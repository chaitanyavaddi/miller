from django.contrib import admin
from django.utils.html import format_html


class ReadOnlyAdmin(admin.ModelAdmin):
    """Staff look at what people did; they don't change it here. Fixing someone's data goes through a shell, on
    purpose, where it leaves a trace."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def short(text: str, length: int = 80) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def pre(text: str):
    """A block of text (JSON, an error) shown as it is."""
    return format_html('<pre style="margin:0;white-space:pre-wrap;max-width:900px">{}</pre>', text) if text else "—"
