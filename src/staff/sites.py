from django.contrib.admin import AdminSite
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from staff.usage import UsageReport

NEXT_AFTER_LOGIN = "next_after_login"   # where Google sign-in sends someone once they're in (accounts.views)


class MillerAdminSite(AdminSite):
    """The way in is the way everyone comes in: Google. Only an account flagged as staff (`manage.py make_staff`)
    gets through; nobody has, or can set, a password here."""
    site_header = "Miller"
    site_title = "Miller admin"
    index_title = "Usage and support"
    index_template = "admin/miller_index.html"

    def has_permission(self, request):
        user = request.user
        return bool(user.is_active and user.is_staff and not user.is_guest)

    def login(self, request, extra_context=None):
        if request.user.is_authenticated and not request.user.is_guest:
            raise PermissionDenied("This account isn't staff.")
        request.session[NEXT_AFTER_LOGIN] = request.GET.get(REDIRECT_FIELD_NAME) or reverse("admin:index")
        return redirect("accounts:login")

    def get_urls(self):
        return [path("usage/", self.admin_view(self.usage), name="usage")] + super().get_urls()

    def index(self, request, extra_context=None):
        return super().index(request, {**(extra_context or {}), "headline": UsageReport.headline()})

    def usage(self, request):
        return TemplateResponse(request, "admin/usage.html", {**self.each_context(request), "title": "Usage", "report": UsageReport.full()})
