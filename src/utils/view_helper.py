from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django_cotton import render_component


class HtmxInfo:
    def __init__(self, request):
        headers = request.headers
        self.request = bool(headers.get("HX-Request"))
        self.target = headers.get("HX-Target")
        self.trigger = headers.get("HX-Trigger")
        self.current_url = headers.get("HX-Current-URL")

    def __bool__(self):
        return self.request


def htmx(request):
    return HtmxInfo(request)


def redirect_to(request, url_or_name):
    url = url_or_name if url_or_name.startswith(("/", "http")) else reverse(url_or_name)
    if htmx(request):
        response = HttpResponse()
        response["HX-Redirect"] = url
        return response
    return redirect(url)


def render_fragment(request, component, context):
    return HttpResponse(render_component(request, component, context))


def render_fragments(request, *components):
    """Several components in one response: the first is the swap target, the rest carry hx-swap-oob."""
    return HttpResponse("".join(render_component(request, name, context) for name, context in components))


def push_url(response, url):
    response["HX-Push-Url"] = url
    return response


def close_overlay(response):
    """Ask the page to dismiss whatever is open in #overlay once this response is swapped in."""
    response["HX-Trigger"] = "overlay-close"
    return response


def refresh_board(response):
    """Ask the board, wherever the request came from, to reload itself once this response lands."""
    response["HX-Trigger"] = '{"board-changed": {"target": "body"}}'
    return response


def retarget(response, selector, swap="outerHTML"):
    """Send a successful form response somewhere other than the form itself."""
    response["HX-Retarget"] = selector
    response["HX-Reswap"] = swap
    return response
