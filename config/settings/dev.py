from ._env import BASE_DIR, load_env_file

load_env_file(".env.dev")

from .base import *  # noqa: E402,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# Without this, the dev server's own static handler answers /static/ before the
# middleware stack and whitenoise never sees it, so dev would differ from prod.
INSTALLED_APPS = ["whitenoise.runserver_nostatic"] + INSTALLED_APPS  # noqa: F405
