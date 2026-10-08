from django.test.runner import DiscoverRunner

from config.settings._env import BASE_DIR

SRC = str(BASE_DIR / "src")


class SrcDiscoverRunner(DiscoverRunner):
    """`manage.py test` with no labels discovers the apps under src/."""

    def __init__(self, *args, **kwargs):
        kwargs["top_level"] = kwargs.get("top_level") or SRC
        super().__init__(*args, **kwargs)

    def build_suite(self, test_labels=None, **kwargs):
        return super().build_suite(test_labels or [SRC], **kwargs)
