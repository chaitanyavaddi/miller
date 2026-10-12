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
- Google console: enable the Calendar API; consent screen "In production" (in "Testing", refresh tokens die after 7 days), scope `calendar.events.readonly` only (sensitive: needs Google's app verification past 100 users); redirect URI `<SITE_URL>/calendars/google/callback/`.
- Azure app: accounts in any org directory and personal Microsoft accounts (multi-tenant), redirect URI `<SITE_URL>/calendars/microsoft/callback/`, delegated `Calendars.Read` + `offline_access`, publisher verification for work accounts.
- Push: Google and Graph only push to https. On an https `SITE_URL` each enabled calendar gets a channel to `<SITE_URL>/calendars/webhook/google/` or `/calendars/webhook/microsoft/`; nothing to register by hand. Locally (http) or if a provider refuses a channel, the scheduled sync keeps calendars fresh.
- Pushes are applied inside the webhook (about half a second), so the database follows the calendar whether or not anyone has Miller open, and open pages hear of it live; if the provider's API stumbles, a background job (`calendars.tasks.sync_source`) tries again. `python manage.py calendar_status` shows each calendar's push channel and when it expires.
- Calendar upkeep runs every ten minutes as a background job (`calendars.tasks.sync_all`): it catches a push that never came, and renews push channels before they lapse (Google's last 7 days, Graph's about 3). `python manage.py sync_calendars` does the same by hand.
- Recordings: a meeting can be recorded in the browser (the mic, plus a shared tab's audio for the call), uploaded in 10-second chunks; `recordings/` holds them. Stopping queues a background job: Groq's Whisper transcribes the tracks (You / Others, merged by clock time), the transcript lands under a note in the meeting's thread, and the audio is deleted. Every 15 minutes a job sweeps up recordings whose tab closed without Stop (or whose job was cut off). Env: `GROQ_API_KEY`, optional `GROQ_MODEL` (`whisper-large-v3`, or `whisper-large-v3-turbo`). To try transcription on a file: `python manage.py import_recording <todo_id> --mic you.m4a --call them.m4a`.
- Scheduled todos: an hourly background job (`scheduled.tasks.produce_all`) puts what's due on each user's list soon after their midnight; the board also does this on load. `python manage.py produce_scheduled` does the same by hand.

## Live updates

The web runs async (ASGI: uvicorn workers under gunicorn), so each signed-in page holds one server-sent-events stream (`/events/`, htmx's `hx-sse:connect`). When meetings change, the server publishes `meetings-changed` the moment the change is saved (`live.bus.publish`): a Postgres `NOTIFY`, which every web process hears on one `LISTEN` connection and passes to that user's open pages, which refresh the board. Calendar pushes are applied inside the webhook (about half a second), and a job every minute closes meetings whose time ran out, so both reach the page live; a ten-minute sync catches any push that never came. Locally, run the site with `DJANGO_SETTINGS_MODULE=config.settings.dev uv run uvicorn config.asgi:application --reload` to see live updates (`runserver` can't hold a stream open; there the page just doesn't get them).

## Background jobs

[django-q2](https://django-q2.readthedocs.io/), with its queue and schedules in the database (no Redis). Every recurring job is listed in `jobs/schedules.py`; `python manage.py ensure_schedules` writes them (the web container runs it on start). One-off jobs, like a recording to transcribe, are queued where they happen. In Docker the cluster runs inside the same container as the web; locally, run `python manage.py qcluster` in a second terminal. Settings: `Q_CLUSTER` in `config/settings/base.py`, `Q_WORKERS` (default 2). Finished and failed jobs are kept in its tables (`django_q_success`, `django_q_failure`).

## Docker

```sh
docker build -t plain .
docker run -p 8000:8000 --env-file .env.prod -v plain-media:/app/media plain   # migrates, schedules, serves, and runs the job worker
```

Prod needs `SECRET_KEY`, `GOOGLE_*`, `SITE_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DB_*`. One container does everything: the web and the job worker run side by side, and if either stops the container exits so the host restarts it. To split the worker out, run a second container from the same image with `worker` (same env, same media volume) and set `RUN_WORKER=0` on the web one.

## Tests

`uv run python manage.py test`
