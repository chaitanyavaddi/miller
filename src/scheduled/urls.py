from django.urls import path

from scheduled.views import ScheduledSaveView, ScheduledView

app_name = "scheduled"

urlpatterns = [
    path("scheduled/",      ScheduledView.as_view(),     name="index"),
    path("scheduled/save/", ScheduledSaveView.as_view(), name="save"),
]
