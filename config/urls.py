from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("home/", TemplateView.as_view(template_name="home.html"), name="home"),
    path("mock/dayplan/", TemplateView.as_view(template_name="mock/dayplan.html"), name="mock-dayplan"),
    path("", include("board.urls")),
    path("", include("accounts.urls")),
    path("", include("comments.urls")),
    path("", include("calendars.urls")),
]
