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

## Scheduled, meetings

Today's list is only for doing: one ordered list of today's todos. Things that repeat, or can't start until a date, live in **Scheduled** (the repeat icon in the top bar): each item is a title, a rhythm (once, every day, weekdays, weekly, monthly, yearly) and an optional time. An item never shows in today's list itself; on each day it falls on, it produces a normal todo at the top of the list, with a "From Scheduled · …" line in its thread. `scheduled/` holds the items, the rhythm rules (`rhythm.py`) and the producer, which runs whenever the board is read (so a new day, a reload or a return after missed days catches up, one todo per item). It never makes a second copy while the last one is still open, and a "once" item goes after it produces.

A produced todo with a time notifies at that time while Miller is open in a tab (a notice, plus a browser notification if allowed). Web push to a closed tab is not built yet: it needs VAPID keys, a service worker and a push sender.

Meetings come from the user's Google and Outlook calendars, read directly through the Google Calendar API and Microsoft Graph, and are listed as read-only todos (`kind=meeting`) after the user's own, for today only and until they end: a thread and comments, no rename/close/delete. Signed-in users connect one or more accounts in Settings (their own Google address is suggested first); each account's primary calendar is on, and its other calendars can be switched on one by one. An account whose access was revoked shows "lost access" with a Reconnect link.

- `calendars/` connections and their calendars (`CalendarSource`), the OAuth consent flow, the Google/Graph clients (`calendar_api.py`), push webhooks, `sync_calendars` command. Each sync re-reads a calendar's next two weeks and makes the list match it; a push notification just triggers that re-read.
- Env: `CALENDAR_TOKEN_KEY` (encrypts refresh tokens; make one with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and never change it, or every account must reconnect); Google reuses `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`; Outlook needs `MICROSOFT_CLIENT_ID`/`MICROSOFT_CLIENT_SECRET`.
- Google console: enable the Calendar API; consent screen "In production" (in "Testing", refresh tokens die after 7 days), scopes `calendar.events` + `calendar.calendarlist.readonly` (sensitive: needs Google's app verification past 100 users); redirect URI `<SITE_URL>/calendars/google/callback/`.
- Azure app: accounts in any org directory and personal Microsoft accounts (multi-tenant), redirect URI `<SITE_URL>/calendars/microsoft/callback/`, delegated `Calendars.ReadWrite` + `offline_access`, publisher verification for work accounts.
- Push: Google and Graph only push to https. On an https `SITE_URL` each enabled calendar gets a channel to `<SITE_URL>/calendars/webhook/google/` or `/calendars/webhook/microsoft/`; nothing to register by hand. Locally (http) or if a provider refuses a channel, the scheduled sync keeps calendars fresh.
- Run `python manage.py sync_calendars` every 15 minutes (`*/15 * * * *`): it renews push channels before they lapse (Google's last 7 days, Graph's about 3), picks up calendars added to an account, and catches missed pushes.
- Scheduled todos: run `python manage.py produce_scheduled` every 15 minutes (`*/15 * * * *`). It puts what's due on each user's list just after their own midnight; safe to rerun. The board also does this on load.

## Docker

```sh
docker build -t plain .
docker run -p 8000:8000 --env-file .env.prod -v plain-media:/app/media plain
```

Prod needs `SECRET_KEY`, `GOOGLE_*`, `SITE_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DB_*`.

## Tests

`uv run python manage.py test`
