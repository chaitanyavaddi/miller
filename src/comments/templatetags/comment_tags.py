from django import template

register = template.Library()


@register.filter
def filesize(size):
    size = int(size or 0)
    if size >= 1024 * 1024:
        return f"{size / 1048576:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"
