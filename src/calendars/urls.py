from django.urls import path

from calendars.views import CalendarCallbackView, CalendarConnectView, RecallWebhookView

app_name = "calendars"

urlpatterns = [
    path("calendars/webhook/",                 RecallWebhookView.as_view(),    name="webhook"),
    path("calendars/<str:provider>/",          CalendarConnectView.as_view(),  name="connect"),
    path("calendars/<str:provider>/callback/", CalendarCallbackView.as_view(), name="callback"),
]
