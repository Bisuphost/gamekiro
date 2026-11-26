import json

from django import template
from django.utils.safestring import mark_safe

from core.seo import absolute_media_url as _absolute_media_url

register = template.Library()

_JSON_SCRIPT_ESCAPES = {
    ord(">"): "\\u003E",
    ord("<"): "\\u003C",
    ord("&"): "\\u0026",
}

register.filter("absolute_media_url", _absolute_media_url)


@register.filter
def ld_json(value):
    return mark_safe(json.dumps(value).translate(_JSON_SCRIPT_ESCAPES))
