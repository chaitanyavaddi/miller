from django.http import HttpResponse
from django.views import View

from accounts.mixins import SignedInRequiredMixin
from accounts.schemas import MeetingsToggleSchema, TimezoneSchema
from accounts.services import PreferenceService
from board.services import SettingsService
from calendars.mixins import CalendarConnectionPermissionRequiredMixin, CalendarSourcePermissionRequiredMixin
from calendars.schemas import SourceToggleSchema
from calendars.services import NOTICE_KEY, CalendarApiError, CalendarAuthError, CalendarService
from utils.view_helper import refresh_board, render_fragment

TEMPLATE = "board.settings"


class SettingsResponseMixin:
    def render_settings(self, notice=""):
        return render_fragment(self.request, TEMPLATE, SettingsService.build(self.request.user, notice))


class SettingsView(SignedInRequiredMixin, SettingsResponseMixin, View):
    def get(self, request):
        return self.render_settings(request.session.pop(NOTICE_KEY, ""))


class SettingsTimezoneView(SignedInRequiredMixin, SettingsResponseMixin, View):
    """Save the zone the list's times read in. The browser also sends its own zone once, on a first
    visit; that never overrides one the user chose."""

    def post(self, request):
        data = TimezoneSchema.from_post(request)
        if data.detected:  # in the background: an unknown zone is just skipped, never a message
            if request.user.timezone or not data.is_known:
                return HttpResponse(status=204)
        elif not data.is_valid():
            return self.render_settings()
        PreferenceService.set_timezone(request.user, data.timezone)
        response = HttpResponse() if data.detected else self.render_settings()
        return refresh_board(response)


class SettingsMeetingsView(SignedInRequiredMixin, SettingsResponseMixin, View):
    def post(self, request):
        data = MeetingsToggleSchema.from_post(request)
        PreferenceService.set_show_meetings(request.user, data.show_meetings)
        return refresh_board(self.render_settings())


class SettingsCalendarDisconnectView(CalendarConnectionPermissionRequiredMixin, SettingsResponseMixin, View):
    permission_required = "calendars.delete_connection"

    def post(self, request, pk):
        email = self.connection.email
        CalendarService.disconnect(self.connection)
        return refresh_board(self.render_settings(f"Disconnected {email}."))


class SettingsCalendarSourceView(CalendarSourcePermissionRequiredMixin, SettingsResponseMixin, View):
    """Switch one calendar of a connected account on or off."""
    permission_required = "calendars.change_source"

    def post(self, request, pk):
        data = SourceToggleSchema.from_post(request)
        try:
            CalendarService.set_enabled(self.source, data.enabled)
        except CalendarAuthError:
            return self.render_settings(f"{self.source.connection.email} lost access. Reconnect it.")
        except CalendarApiError:
            return self.render_settings("Couldn't reach the calendar. Try again in a minute.")
        return refresh_board(self.render_settings())
