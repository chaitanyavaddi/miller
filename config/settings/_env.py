"""Env helpers. Secrets come from the environment (.env.dev locally, real env vars in
prod) and never have a fallback; a missing one fails at startup, not at 2am."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_MISSING = object()


def load_env_file(filename):
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(BASE_DIR / filename, override=False)


def env(key, default=_MISSING):
    value = os.environ.get(key, default)
    if value is _MISSING:
        raise ImproperlyConfigured(f"{key} is not set. See .env.example.")
    return value


def env_bool(key, default=False):
    return str(env(key, str(default))).strip().lower() in ("1", "true", "yes", "on")


def env_list(key, default=""):
    return [v.strip() for v in env(key, default).split(",") if v.strip()]
