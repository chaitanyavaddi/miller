import json

from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rules.contrib.views import PermissionRequiredMixin

from accounts.mixins import SignedInRequiredMixin
from calendars.models import CalendarConnection
from calendars.services import NOTICE_KEY, CalendarApiError, CalendarOAuthService, CalendarService
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
        except CalendarApiError:
            return self.back_to_settings("Couldn't reach the calendar. Try again in a minute.")
        return self.back_to_settings(f"Connected {connection.email}.")


@method_decorator(csrf_exempt, name="dispatch")
class CalendarWebhookView(View):
    """Push notifications that a calendar changed: we re-read it. Each carries the secret its channel
    was opened with, so anyone else's POST does nothing. A failed sync answers 502 so the provider
    retries it."""

    def post(self, request, provider):
        if provider == CalendarConnection.Provider.GOOGLE:
            return self.google(request)
        if provider == CalendarConnection.Provider.MICROSOFT:
            return self.microsoft(request)
        raise Http404("No such calendar provider")

    def google(self, request):
        if request.headers.get("X-Goog-Resource-State") == "sync":
            return HttpResponse(status=200)  # a new channel's hello (before we've even saved it): nothing changed yet
        source = CalendarService.source_for_push(
            CalendarConnection.Provider.GOOGLE,
            channel_id=request.headers.get("X-Goog-Channel-ID", ""),
            secret=request.headers.get("X-Goog-Channel-Token", ""),
        )
        return self.sync([source] if source is not None else [])

    def microsoft(self, request):
        if "validationToken" in request.GET:  # Graph checks the address when a subscription is made
            return HttpResponse(request.GET["validationToken"], content_type="text/plain")
        try:
            notices = json.loads(request.body).get("value") or []
        except (ValueError, AttributeError):
            return HttpResponse(status=400)
        sources = {}
        for notice in notices:
            source = CalendarService.source_for_push(
                CalendarConnection.Provider.MICROSOFT, resource=notice.get("subscriptionId", ""), secret=notice.get("clientState", ""),
            )
            if source is not None:
                sources[source.pk] = source
        return self.sync(sources.values(), status=202)

    def sync(self, sources, status=200):
        try:
            for source in sources:
                CalendarService.sync_source(source)
        except CalendarApiError:
            return HttpResponse(status=502)
        return HttpResponse(status=status)
