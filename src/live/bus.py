"""Live events: the server tells a user's open pages that something changed, the moment it changes.

publish() fires once the change is saved. On Postgres it's a NOTIFY on one channel, so it reaches every
web process (and comes from the worker too); each web process keeps one LISTEN connection and hands
events to its open streams for that user. Elsewhere (SQLite in dev and tests) it's delivered within the
process. A page's stream is /events/ (live.views)."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from django.conf import settings
from django.db import connection, transaction

logger = logging.getLogger(__name__)

CHANNEL = "miller_events"
RETRY_LISTEN = 2  # seconds before the listener reconnects after losing Postgres

_subscribers: dict[int, set[asyncio.Queue]] = {}
_loop: asyncio.AbstractEventLoop | None = None
_listener: asyncio.Task | None = None


def on_postgres() -> bool:
    return settings.DATABASES["default"]["ENGINE"].endswith("postgresql")


def publish(user_id: int, name: str, data: dict | None = None) -> None:
    """Tell the user's open pages that `name` happened (e.g. "meetings-changed"), once this is saved.
    `data` rides along as JSON for events that carry something (a voice reply); keep it small, as a
    Postgres NOTIFY holds under 8000 bytes."""
    transaction.on_commit(lambda: _send(user_id, name, data))


def _send(user_id: int, name: str, data: dict | None = None) -> None:
    payload = f"{user_id}:{name}:{json.dumps(data, separators=(',', ':'))}" if data else f"{user_id}:{name}"
    if on_postgres():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, payload])
    else:
        deliver(payload)


def deliver(payload: str) -> None:
    """Hand an event to this process's open streams for its user. Safe from any thread."""
    user, _, rest = payload.partition(":")
    name, _, data = rest.partition(":")
    queues = _subscribers.get(int(user)) if user.isdigit() else None
    if not queues or _loop is None or _loop.is_closed():
        return
    for queue in list(queues):
        _loop.call_soon_threadsafe(_offer, queue, (name, data or name))


def _offer(queue: asyncio.Queue, event: tuple[str, str]) -> None:
    if not queue.full():  # a stream far behind gets the next one; it only ever needs "something changed"
        queue.put_nowait(event)


@asynccontextmanager
async def subscription(user_id: int):
    """A queue of this user's events, as (name, data), for as long as a page's stream is open."""
    global _loop
    _loop = asyncio.get_running_loop()
    _ensure_listener()
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _subscribers.setdefault(user_id, set()).add(queue)
    try:
        yield queue
    finally:
        queues = _subscribers.get(user_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                _subscribers.pop(user_id, None)


def _ensure_listener() -> None:
    global _listener
    if on_postgres() and (_listener is None or _listener.done()):
        _listener = asyncio.get_running_loop().create_task(_listen())


async def _listen() -> None:
    """One LISTEN connection per web process, for as long as it runs; reconnects if Postgres drops it."""
    import psycopg
    db = settings.DATABASES["default"]
    options = db.get("OPTIONS", {})
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                dbname=db["NAME"], user=db["USER"], password=db["PASSWORD"], host=db["HOST"], port=db.get("PORT") or 5432,
                sslmode=options.get("sslmode", "prefer"), autocommit=True,
            ) as conn:
                await conn.execute(f"LISTEN {CHANNEL}")
                async for notice in conn.notifies():
                    deliver(notice.payload)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - keep listening whatever went wrong
            logger.warning("Live events listener lost Postgres (%s); reconnecting", error)
            await asyncio.sleep(RETRY_LISTEN)
