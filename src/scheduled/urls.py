from django.urls import path

from scheduled.views import MoveView, ScheduledSaveView, ScheduledView

app_name = "scheduled"

urlpatterns = [
    path("scheduled/",      ScheduledView.as_view(),     name="index"),
    path("scheduled/save/", ScheduledSaveView.as_view(), name="save"),
    path("scheduled/move/<int:pk>/", MoveView.as_view(), name="move"),
]
