from ._env import BASE_DIR, env

SECRET_KEY = env("SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = []

INSTALLED_APPS = [
    "django_cotton",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rules.apps.AutodiscoverRulesConfig",
    "accounts",
    "todos",
    "comments",
    "calendars",
    "scheduled",
    "board",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.TimezoneMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
        "builtins": [
            "django.templatetags.static",
            "todos.templatetags.todo_tags",
            "comments.templatetags.comment_tags",
        ],
    },
}]

# Google is the only way in: no password validators, no password routes. A User row
# has an unusable password, so even a stray form post could not authenticate one.
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "rules.permissions.ObjectPermissionBackend",
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "accounts:login"
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", "")
# Calendars: we run each provider's consent screen and call Google Calendar / Microsoft Graph
# ourselves. Microsoft is optional; Google reuses the sign-in client (add the calendar callback as
# a redirect URI there). Refresh tokens are encrypted with CALENDAR_TOKEN_KEY (a Fernet key);
# without it, calendars can't be connected.
MICROSOFT_CLIENT_ID = env("MICROSOFT_CLIENT_ID", "")
MICROSOFT_CLIENT_SECRET = env("MICROSOFT_CLIENT_SECRET", "")
CALENDAR_TOKEN_KEY = env("CALENDAR_TOKEN_KEY", "")
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

SESSION_COOKIE_AGE = 60 * 60 * 24 * 365  # a guest's identity is this cookie

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = False
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploads are served through an owner-checked view, never straight off MEDIA_ROOT.
MEDIA_ROOT = BASE_DIR / "media"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

TEST_RUNNER = "config.test_runner.SrcDiscoverRunner"
