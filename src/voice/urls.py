from django.urls import path

from voice.views import VoiceCommandView, VoiceUndoView

app_name = "voice"

urlpatterns = [
    path("voice/command/",           VoiceCommandView.as_view(), name="command"),
    path("voice/<int:pk>/undo/",     VoiceUndoView.as_view(),    name="undo"),
]
