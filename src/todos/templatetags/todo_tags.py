from django import template

register = template.Library()


@register.filter
def pad2(number):
    return f"{int(number):02d}"
