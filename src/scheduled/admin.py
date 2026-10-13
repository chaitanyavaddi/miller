from django.contrib import admin

from scheduled.models import ScheduledItem
from staff.base import ReadOnlyAdmin


@admin.register(ScheduledItem)
class ScheduledItemAdmin(ReadOnlyAdmin):
    list_display = ("title", "user", "rhythm", "on_date", "weekdays", "month_day", "last_produced_on", "created_at")
    list_filter = ("rhythm", "created_at")
    search_fields = ("title", "user__email")
    list_select_related = ("user",)
