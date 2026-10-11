from django.urls import path

from calendars.views import CalendarCallbackView, CalendarConnectView, CalendarWebhookView

app_name = "calendars"

urlpatterns = [
    path("calendars/webhook/<str:provider>/",  CalendarWebhookView.as_view(),  name="webhook"),
    path("calendars/<str:provider>/",          CalendarConnectView.as_view(),  name="connect"),
    path("calendars/<str:provider>/callback/", CalendarCallbackView.as_view(), name="callback"),
]
