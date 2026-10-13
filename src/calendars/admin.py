from django.contrib import admin

from calendars.models import CalendarConnection, CalendarSource
from staff.base import ReadOnlyAdmin, ReadOnlyInline


class SourceInline(ReadOnlyInline):
    model = CalendarSource
    fields = ("name", "is_primary", "enabled", "channel_expires_at")   # no channel ids
    readonly_fields = fields


@admin.register(CalendarConnection)
class CalendarConnectionAdmin(ReadOnlyAdmin):
    """Who has a calendar connected, and whether it's still working. The token is never shown."""
    list_display = ("email", "user", "provider", "status", "synced_at", "created_at")
    list_filter = ("status", "provider")
    search_fields = ("email", "user__email")
    list_select_related = ("user",)
    exclude = ("refresh_token",)
    inlines = [SourceInline]
