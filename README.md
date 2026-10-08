# Miller

A quiet todo list: ordered todos on the left, a thread of comments and files on the right.
Visitors start as guests; Google is the only way to sign in.

**Stack:** uv · Django · django-cotton (components) · htmx 4 · plain CSS · SQLite (dev) / Postgres (prod)

## Run

```sh
uv sync
# .env.dev: set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (see .env.example)
uv run python manage.py migrate
uv run python manage.py runserver
```

Google console → OAuth client (Web) → authorised redirect URI: `http://localhost:8000/auth/google/callback/`.

## Layout

Conventions follow `.claude/patterns.md`. Apps live in `src/`, one per domain:

- `accounts/` identity: `User`, guest sessions, Google OAuth. Emits `guest_merged` when a guest signs in to an existing account; it knows nothing about todos.
- `todos/` the ordered list: `Todo`, per-day position history, stats, `todos.*` permissions. Moves a merged guest's todos via a receiver.
- `comments/` a todo's thread: `Comment`, `Attachment`, downloads, `comments.*` permissions.
- `board/` the home screen. Composes todos and comments: views, URLs and the read model (`BoardService`).
- `utils/view_helper.py` `htmx()`, `redirect_to()`, fragment rendering, `HX-*` response headers.

Each domain app has `models.py`, `schemas.py` (dataclass input + `is_valid()`, errors via messages), `services` (static methods), `rules.py` and `mixins.py`.
Templates: `templates/<app>/` pages and `templates/cotton/<domain>/` components. CSS: `static/css/main.css` plus one file per domain. JS: `static/js/main.js` plus one per domain.

## Docker

```sh
docker build -t plain .
docker run -p 8000:8000 --env-file .env.prod -v plain-media:/app/media plain
```

Prod needs `SECRET_KEY`, `GOOGLE_*`, `SITE_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DB_*`.

## Tests

`uv run python manage.py test`
