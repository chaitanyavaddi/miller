# Miller

A todo list for today. Todos and today's meetings in one ordered list; a thread of updates under each.
Live at https://miller.so.

**Stack:** Python 3.12 · uv · Django 6 · django-cotton · htmx 4 · plain CSS · Postgres (prod) / SQLite (dev) ·
django-q2 (background jobs) · uvicorn (async web, for live updates).

---

## Run it locally

```sh
uv sync                                   # install
cp .env.example .env.dev                  # then fill it in (see Environment)
uv run python manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings.dev uv run uvicorn config.asgi:application --reload --timeout-graceful-shutdown 1 --port 8000
uv run python manage.py qcluster          # second terminal: background jobs
```

Open http://localhost:8000. (`uv run python manage.py runserver` also works, minus live updates.)

Tests: `uv run python manage.py test`

Settings: `config/settings/dev.py` (reads `.env.dev`) and `prod.py` (reads real env vars). `manage.py` uses dev.

---

## Environment

| Variable | Needed | What |
|---|---|---|
| `SECRET_KEY` | always | Django secret. Any long random string. |
| `SITE_URL` | always | `http://localhost:8000` locally, `https://miller.so` in prod. Push webhooks need https. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | sign-in, calendars | Google OAuth client (below). |
| `CALENDAR_TOKEN_KEY` | calendars | Encrypts calendar tokens. Make once, **never change** (everyone would have to reconnect):<br>`uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `RESEND_API_KEY` | email sign-in | Resend (https://resend.com) API key: sends the 6-digit sign-in codes (sign in with Google, or with a code emailed to you; no passwords). Without it, production offers only Google; in dev the codes are logged to the console. |
| `MAIL_FROM` | optional | Sender of those emails (default `Miller <hello@updates.miller.so>`); its domain must be verified in Resend. |
| `GROQ_API_KEY` | voice notes, call recordings | Groq console. |
| `GROQ_MODEL` | optional | `whisper-large-v3` (default) or `whisper-large-v3-turbo` (cheaper). |
| `OPENROUTER_API_KEY` | voice mode | OpenRouter key. Without it (or Groq's), voice mode isn't offered. |
| `VOICE_GUEST_BUDGET_USD` / `VOICE_MEMBER_BUDGET_USD` | optional | What each person may spend on voice in all, in USD (default `1.00` each). Their spend shows in Settings → AI usage and in voice mode's footer. `uv run python manage.py voice_spend` lists it per user. |
| `ADMIN_URL` | prod | Where the admin lives (default `admin/`). In production set something nobody would guess, e.g. `ops-8f3k2/`. |
| `GROQ_COST_PER_HOUR` | optional | Speech is priced at this rate for the usage figure: `0.111` (whisper-large-v3, default) or `0.04` (turbo). |
| `VOICE_DECISION_MODEL` | optional | Any OpenRouter slug that supports structured output. Default `anthropic/claude-haiku-5.5`; change it any time, no code change. |
| `MICROSOFT_CLIENT_ID` / `_SECRET` | optional | Outlook calendars. Built, but hidden in Settings (`OFFERED_PROVIDERS` in `board/services/settings_service.py`). |
| `DB_NAME` `DB_USER` `DB_PASSWORD` `DB_HOST` `DB_PORT` `DB_SSLMODE` | prod | Postgres. `DB_SSLMODE` defaults to `require`. |
| `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` | prod | Comma-separated, e.g. `miller.so` / `https://miller.so`. |
| `TIME_ZONE` | optional | Server default; users set their own. |
| `Q_WORKERS` / `WEB_CONCURRENCY` | optional | Job workers (2) / web workers (2). |
| `RUN_WORKER` | optional | `0` to run the job worker as its own container instead. |

---

## Outside services

**Google Cloud** (project `miller-511305`), one OAuth client (Web) for sign-in and calendars:
- Authorised redirect URIs: `<SITE_URL>/auth/google/callback/` and `<SITE_URL>/calendars/google/callback/` (add both, for localhost and miller.so).
- Enable **Google Calendar API**.
- Auth Platform → Audience: **In production** (in Testing, tokens die after 7 days).
- Data access: only `calendar.events.readonly` (plus email, profile, openid). Verification needs a privacy page (`/privacy/`), terms (`/terms/`), verified domain, and a demo video.
- Calendar push notifications need nothing extra: Miller registers them itself (https only).

**Resend** (https://resend.com): add the sending domain (updates.miller.so, verified) and its DNS records once, then an API key → `RESEND_API_KEY`. Only sign-in codes are sent.

**Groq** (https://console.groq.com): API key → `GROQ_API_KEY`. Whisper transcribes voice notes (≤1 min, nothing kept) and call recordings (audio deleted after). Free tier ~2 h audio/hour.

**Microsoft Azure** (optional, Outlook): multi-tenant app, redirect `<SITE_URL>/calendars/microsoft/callback/`, delegated `Calendars.Read` + `offline_access`.

**Postgres**: any. Prod only. Live updates use its `LISTEN/NOTIFY`.

**Dokploy** (hosting): one application from this repo's `Dockerfile`, env vars as above, a volume at `/app/media`, domain miller.so (https). No crons, no second service.

---

## Deploy

Push to `main`; Dokploy builds and redeploys. The one container (`docker-entrypoint.sh`):
1. migrates, writes the job schedules (`ensure_schedules`),
2. runs the web (gunicorn + uvicorn workers, `config/asgi.py`) and the job worker (`qcluster`) side by side,
3. exits if either stops, so Dokploy restarts it.

```sh
docker build -t miller .
docker run -p 8000:8000 --env-file .env.prod -v miller-media:/app/media miller
```

---

## How the moving parts work

- **Meetings**: Google pushes changes to `/calendars/webhook/google/`; the webhook re-reads that calendar (~0.5 s). A job every 10 min re-syncs everything (catches a missed push, renews push channels, which expire after 7 days).
- **Live updates**: when meetings change, the server sends a Postgres `NOTIFY`; each web process listens and pushes `meetings-changed` to that user's open pages over `/events/` (server-sent events). The page refreshes its board.
- **Background jobs** (django-q2, queue in Postgres, no Redis). Recurring ones are listed in `jobs/schedules.py`:
  - Close ended meetings: every minute.
  - Sync calendars: every 10 min.
  - Process stuck recordings: every 15 min.
  - Produce scheduled todos: hourly.
  One-off: transcribe a stopped call; retry a calendar sync.
- **Recordings**: a meeting's call (mic + a shared tab) uploads in 10 s chunks to `media/recordings/`; on Stop, a job sends it to Groq, saves the transcript (meeting ⋮ → Transcript), deletes the audio.
- **Voice notes**: the composer's mic; audio goes to Groq and the words come back into the composer. Nothing stored.

---

## Commands

```sh
uv run python manage.py calendar_status     # each calendar's push channel (want "push live")
uv run python manage.py sync_calendars      # re-sync all calendars now
uv run python manage.py process_recordings  # transcribe stopped/stuck recordings now
uv run python manage.py import_recording <todo_id> --mic a.m4a --call b.m4a   # test transcription
uv run python manage.py produce_scheduled   # put today's scheduled todos on lists now
uv run python manage.py prune_guests --days 7   # delete guests who never added a todo (not scheduled)
uv run python manage.py ensure_schedules    # rewrite job schedules (done on every start)
```

In Dokploy, run these from the app's Terminal without `uv run`.

---

## Code layout

`src/`, one app per domain; each has `models.py`, `schemas.py`, `services/`, `rules.py`, `mixins.py`, `views.py`.

| App | Holds |
|---|---|
| `accounts` | users, guests, sign-in (Google, or a code emailed to you); sessions last 400 days, moved on daily while used |
| `todos` | the ordered list, meetings' place in it |
| `comments` | threads (updates), links |
| `board` | the main screen, settings, home and legal pages' routes |
| `calendars` | calendar connections, Google/Graph clients, webhook, sync |
| `scheduled` | repeating items, Move to… |
| `recordings` | call recordings, voice notes, Groq |
| `voice` | voice mode: a spoken command → todo changes (model on OpenRouter), logged and undoable |
| `live` | the event bus and `/events/` stream |
| `jobs` | the list of recurring jobs |

Templates: `templates/` (pages) and `templates/cotton/` (components). CSS/JS: `static/`.

---

## Admin: who uses Miller, what they do, what's going wrong

A read-only Django admin at `/<ADMIN_URL>` (`staff/`). There are no passwords: you sign in with Google as always, and an account flagged as staff is let through.

```
# once, on the server (a container shell): let yourself in. If you haven't signed in yet, this makes the account.
python manage.py make_staff you@company.com        # --revoke takes it away
```
Then sign in with Google and open the admin's address.

- **Front page:** people, who was active (24h / 7d / 30d), todos and voice in the last day, and links to whatever is failing.
- **Usage report** (`/<ADMIN_URL>usage/`): voice outcomes, failure rate, how long answers take, spend, a day-by-day table and the biggest spenders.
- **Users:** each person's counts (todos, open, voice, spend) with their latest todos and what they said; filter guests and staff.
- **Todos, notes:** every todo with its thread; filter open, done or deleted; search by words or email.
- **Voice commands:** what was said, what the model asked for (as JSON), what was done, how long each stage took and what it cost. Filter failed, slow (2s, 4s, 8s) or by model.
- **Recordings, calendars, jobs:** how they're getting on (state, attempts, errors) for debugging. Call transcripts, calendar tokens and uploaded files are never shown.
- Everything is **read-only** (to change data, use a shell, where it leaves a trace). The background-job tables are Django-Q's own.

---

## When something's off

| Symptom | Look at |
|---|---|
| Meetings don't update | `calendar_status`; logs for `POST /calendars/webhook/google/` and `[Q] … Sync calendar`. |
| Page doesn't update live | logs for `Live events listener lost Postgres`; is uvicorn running (not runserver)? |
| Jobs never run | logs for `Q Cluster … running`. |
| Voice mode missing, or no replies | both `GROQ_API_KEY` and `OPENROUTER_API_KEY` set? Replies stream over `/events/`, so it needs uvicorn too. `VoiceCommand` rows hold each transcript, the model's answer and timings. |
| Transcription fails | `GROQ_API_KEY`; recording's `error` field; Groq error 1010 = missing User-Agent. |
| Calendar "Lost access" | user revoked or token expired: Reconnect in Settings. If everyone: was `CALENDAR_TOKEN_KEY` changed? |
| Google sign-in "redirect_uri_mismatch" | add the exact URI to the OAuth client. |
