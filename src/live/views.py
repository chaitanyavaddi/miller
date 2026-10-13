import asyncio

from django.core.handlers.asgi import ASGIRequest
from django.http import HttpResponse, StreamingHttpResponse

from live.bus import subscription

HEARTBEAT = 20  # seconds: a comment line keeps proxies from closing a quiet stream


async def events(request):
    """A signed-in page's live stream (server-sent events): `event: meetings-changed` and the like, the
    moment they happen. Only under the async server; the sync dev server can't hold a stream open."""
    if not isinstance(request, ASGIRequest):
        return HttpResponse(status=204)
    user = await request.auser()
    if not user.is_authenticated or user.is_guest:
        return HttpResponse(status=204)

    async def stream():
        async with subscription(user.pk) as queue:
            yield "retry: 3000\n\n"
            while True:
                try:
                    name, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"event: {name}\ndata: {data}\n\n"

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # no proxy buffering: each event goes out as it's written
    return response
