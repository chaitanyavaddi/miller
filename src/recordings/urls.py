from django.urls import path

from recordings.views import RecordingChunkView, RecordingStartView, RecordingStatusView, RecordingStopView, TranscriptView, VoiceView

app_name = "recordings"

urlpatterns = [
    path("todos/<int:pk>/recordings/start/", RecordingStartView.as_view(), name="start"),
    path("todos/<int:pk>/voice/",            VoiceView.as_view(),          name="voice"),
    path("todos/<int:pk>/transcript/",       TranscriptView.as_view(),     name="transcript"),
    path("recordings/<int:pk>/chunks/",      RecordingChunkView.as_view(), name="chunk"),
    path("recordings/<int:pk>/stop/",        RecordingStopView.as_view(),  name="stop"),
    path("recordings/<int:pk>/",             RecordingStatusView.as_view(), name="status"),
]
