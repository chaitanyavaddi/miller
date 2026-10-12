import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# A web address in an update: http(s)://… or www.…, up to whitespace. Trailing punctuation (a full
# stop, a closing bracket) is the sentence's, not the link's.
URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
TRAILING = ".,;:!?)]}'\""
LINK_TEXT_MAX = 48
OUT_ICON = ('<svg class="comment__link-out" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 16L16 8"></path><path d="M9.5 8H16v6.5"></path></svg>')


@register.filter
def filesize(size):
    size = int(size or 0)
    if size >= 1024 * 1024:
        return f"{size / 1048576:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


@register.filter
def linkify(text):
    """An update's text, escaped, with its web addresses as links that open in a new tab: shown without
    the scheme, shortened if long, and marked with an outward arrow."""
    out, last = [], 0
    for match in URL.finditer(text or ""):
        url = match.group(0).rstrip(TRAILING)
        start, end = match.start(), match.start() + len(url)
        out.append(escape(text[last:start]))
        href = url if url.lower().startswith("http") else f"https://{url}"
        shown = re.sub(r"^(?:https?://)?(?:www\.)?", "", url, flags=re.IGNORECASE).rstrip("/")
        if len(shown) > LINK_TEXT_MAX:
            shown = shown[:LINK_TEXT_MAX - 1] + "…"
        out.append(f'<a class="comment__link" href="{escape(href)}" target="_blank" rel="noopener noreferrer">{escape(shown)}{OUT_ICON}</a>')
        last = end
    out.append(escape((text or "")[last:]))
    return mark_safe("".join(out))


@register.filter
def clock(seconds):
    """A transcript line's time into the recording: 04:12, or 1:02:05 past the hour."""
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
