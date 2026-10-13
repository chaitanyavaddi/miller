from django.apps import AppConfig


class VoiceConfig(AppConfig):
    name = "voice"

    def ready(self):
        from voice import receivers  # noqa: F401
