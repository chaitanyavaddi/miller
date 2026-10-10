import json

from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rules.contrib.views import PermissionRequiredMixin

from accounts.mixins import SignedInRequiredMixin
from calendars.models import CalendarConnection
from calendars.services import NOTICE_KEY, CalendarOAuthService, CalendarService, RecallError, RecallService
from utils.view_helper import redirect_to

SETTINGS_URL = "board:index"


class CalendarFlowMixin(SignedInRequiredMixin, PermissionRequiredMixin):
    """The connect flow: members only, for a provider we know, ending back in the settings modal."""
    permission_required = "calendars.connect_calendar"

    def dispatch(self, request, *args, **kwargs):
        if kwargs.get("provider") not in CalendarConnection.Provider.values:
            raise Http404("No such calendar provider")
        return super().dispatch(request, *args, **kwargs)

    def back_to_settings(self, notice):
        self.request.session[NOTICE_KEY] = notice
        return redirect_to(self.request, reverse(SETTINGS_URL) + "?settings=1")


class CalendarConnectView(CalendarFlowMixin, View):
    def get(self, request, provider):
        if not CalendarService.can_connect(provider):
            return self.back_to_settings("Calendar connections aren't set up on this server yet.")
        state = CalendarOAuthService.issue_state(request.session, provider)
        return redirect_to(request, CalendarOAuthService.authorization_url(provider, state, request.GET.get("hint", "").strip()))


class CalendarCallbackView(CalendarFlowMixin, View):
    def get(self, request, provider):
        if request.GET.get("error"):
            return self.back_to_settings("Connecting the calendar was cancelled.")
        if not CalendarOAuthService.consume_state(request.session, provider, request.GET.get("state", "")):
            return self.back_to_settings("That link expired. Try connecting again.")
        grant = CalendarOAuthService.exchange(provider, request.GET.get("code", ""))
        if grant is None:
            return self.back_to_settings("The calendar didn't grant access. Try again.")
        try:
            connection = CalendarService.connect(request.user, provider, grant)
        except RecallError:
            return self.back_to_settings("Couldn't reach the calendar service. Try again in a minute.")
        return self.back_to_settings(f"Connected {connection.email}.")


@method_decorator(csrf_exempt, name="dispatch")
class RecallWebhookView(View):
    """Recall's calendar webhooks. Signed, so anyone else's POST is refused; a failed sync answers
    502 so Recall retries it."""

    def post(self, request):
        if not RecallService.verify_webhook(request.headers, request.body):
            return HttpResponse(status=401)
        try:
            payload = json.loads(request.body)
        except ValueError:
            return HttpResponse(status=400)
        try:
            CalendarService.handle_webhook(payload)
        except RecallError:
            return HttpResponse(status=502)
        return HttpResponse(status=204)
