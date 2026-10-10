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

## Days, reminders, meetings

The list is filed by day: today under the heading, then the next three days (always shown, empty or not), then a folded "later". Unfinished todos roll to the top of today at midnight. A todo can carry a reminder (it files the todo under that day; sending it is not wired up yet). Days are counted in the user's time zone: the browser's is saved on a first visit, and it can be changed in Settings (avatar menu).

Meetings come from calendars synced by [Recall.ai](https://docs.recall.ai/docs/calendar-v2-integration-guide) and are listed as read-only todos (`kind=meeting`): a thread and comments, no rename/close/delete, and they move only within their own day. Signed-in users connect one or more Google or Outlook calendars in Settings; their own Google address is suggested first.

- `calendars/` connections, the OAuth consent flow, the Recall client and webhook, `sync_calendars` command.
- Env: `RECALL_API_KEY`, `RECALL_API_BASE` (your Recall region, default `https://us-east-1.recall.ai`), `RECALL_WEBHOOK_SECRET`; Google reuses `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`; Outlook needs `MICROSOFT_CLIENT_ID`/`MICROSOFT_CLIENT_SECRET`.
- Google console: enable the Calendar API, add the `calendar.events.readonly` scope to the consent screen, and add the redirect URI `<SITE_URL>/calendars/google/callback/`. Azure app: redirect URI `<SITE_URL>/calendars/microsoft/callback/`, delegated `Calendars.Read` + `offline_access`.
- Recall dashboard: webhook endpoint `<SITE_URL>/calendars/webhook/` for `calendar.sync_events` and `calendar.update`; its signing secret is `RECALL_WEBHOOK_SECRET`.
- Optional fallback for missed webhooks: run `python manage.py sync_calendars` on a schedule.

## Docker

```sh
docker build -t plain .
docker run -p 8000:8000 --env-file .env.prod -v plain-media:/app/media plain
```

Prod needs `SECRET_KEY`, `GOOGLE_*`, `SITE_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DB_*`.

## Tests

`uv run python manage.py test`
