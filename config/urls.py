from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("home/", RedirectView.as_view(url="/", permanent=True)),
    path("privacy/", TemplateView.as_view(template_name="legal/privacy.html"), name="privacy"),
    path("terms/", TemplateView.as_view(template_name="legal/terms.html"), name="terms"),
    path("mock/dayplan/", TemplateView.as_view(template_name="mock/dayplan.html"), name="mock-dayplan"),
    path("", include("board.urls")),
    path("", include("accounts.urls")),
    path("", include("comments.urls")),
    path("", include("calendars.urls")),
    path("", include("scheduled.urls")),
    path("", include("recordings.urls")),
    path("", include("voice.urls")),
    path("", include("live.urls")),
]
