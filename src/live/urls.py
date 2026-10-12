from django.urls import path

from live.views import events

app_name = "live"

urlpatterns = [
    path("events/", events, name="events"),
]
