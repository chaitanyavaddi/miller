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
    "staff.apps.MillerAdminConfig",   # Django's admin as Miller's own site: see staff/
    "django.contrib.staticfiles",
    "rules.apps.AutodiscoverRulesConfig",
    "accounts",
    "todos",
    "comments",
    "calendars",
    "scheduled",
    "recordings",
    "voice",
    "jobs",
    "live",
    "board",
    "django_q",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.KeepSignedInMiddleware",
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

# Two ways in: Google, or a 6-digit code emailed to the address (accounts.services.CodeService). No passwords: a User
# row has an unusable one, so even a stray form post could not authenticate one.
# Sign-in codes go out through Resend (https://resend.com). Without a key, codes are logged instead (dev only).
RESEND_API_KEY = env("RESEND_API_KEY", "")
MAIL_FROM = env("MAIL_FROM", "Miller <hello@updates.miller.so>")
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
# Recordings are transcribed by Groq's Whisper; without a key, nothing is (and Record isn't offered).
GROQ_API_KEY = env("GROQ_API_KEY", "")
GROQ_MODEL = env("GROQ_MODEL", "whisper-large-v3")  # or whisper-large-v3-turbo: cheaper, a little less accurate

# Voice mode: what's said is turned into todo changes by a model on OpenRouter. Change VOICE_DECISION_MODEL
# any time (any OpenRouter slug that supports structured output); without a key, voice mode isn't offered.
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "")
VOICE_DECISION_MODEL = env("VOICE_DECISION_MODEL", "anthropic/claude-haiku-5.5")
# Each command's cost is recorded (OpenRouter reports the model's; the speech is priced at Groq's rate per hour
# of audio, with its 10-second minimum per request). Each person may spend this much in all, in USD, then voice
# stops until it's raised. Settings shows what's been spent.
VOICE_GUEST_BUDGET_USD = env("VOICE_GUEST_BUDGET_USD", "1.00")
VOICE_MEMBER_BUDGET_USD = env("VOICE_MEMBER_BUDGET_USD", "1.00")
GROQ_COST_PER_HOUR = env("GROQ_COST_PER_HOUR", "0.111")  # whisper-large-v3; 0.04 for -turbo
GROQ_MIN_BILLED_SECONDS = 10

# Background jobs: django-q2, its queue and schedules in the database (no Redis). The cluster runs as its
# own process: `python manage.py qcluster`. Recurring jobs are listed in jobs/schedules.py.
Q_CLUSTER = {
    "name":         "miller",
    "orm":          "default",
    "workers":      int(env("Q_WORKERS", "2")),
    "timeout":      900,     # a job running longer than 15 minutes is stopped (an hour's audio takes well under one)
    "retry":        960,     # must exceed timeout
    "max_attempts": 1,       # recordings keep their own attempts and recover through the 15-minute sweep
    "poll":         5,       # seconds between looks at the queue table: light on the database
    "save_limit":   200,     # finished jobs kept in django_q_success
    "catch_up":     False,   # after downtime, run a missed schedule once, not once per missed slot
    "label":        "Jobs",
}
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
# Where the admin lives. Set it to something nobody would guess in production (ADMIN_URL=ops-8f3k2/); getting in takes a
# Google sign-in on an account flagged with `manage.py make_staff`, so this is one lock of two.
ADMIN_URL = env("ADMIN_URL", "admin/").strip("/") + "/"

# Signed in stays signed in: the cookie lasts 400 days (the most browsers keep one), and KeepSignedInMiddleware moves
# that on at most once a day while someone keeps using Miller, so only a year of not coming back signs them out.
# A guest's identity is this cookie too.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 400

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
