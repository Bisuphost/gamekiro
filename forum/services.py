import re
from datetime import timedelta

import bleach
from django.conf import settings
from django.utils import timezone

RATE_LIMIT_WINDOW = timedelta(minutes=1)
RATE_LIMIT_MAX = {"thread": 3, "post": 10, "postimage": 10}

ALLOWED_BODY_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "u",
    "s",
    "sup",
    "sub",
    "blockquote",
    "ul",
    "ol",
    "li",
    "a",
    "code",
    "pre",
    "h1",
    "h2",
    "h3",
    "img",
    "iframe",
    "span",
]

YOUTUBE_EMBED_RE = re.compile(r"^https://www\.youtube\.com/embed/[\w-]+(\?.*)?$")
VIMEO_EMBED_RE = re.compile(r"^https://player\.vimeo\.com/video/\d+(\?.*)?$")
ALLOWED_SPAN_CLASSES = {"ql-spoiler-true"}


def _allowed_image_src_prefixes():
    prefixes = [settings.MEDIA_URL]
    custom_domain = getattr(settings, "AWS_S3_CUSTOM_DOMAIN", None)
    if custom_domain:
        prefixes.append(f"https://{custom_domain}/")
    endpoint_url = getattr(settings, "AWS_S3_ENDPOINT_URL", None)
    if endpoint_url:
        prefixes.append(endpoint_url)
    return tuple(prefixes)


def _allow_img_attribute(tag, name, value):
    if name == "alt":
        return True
    if name == "src":
        return value.startswith(_allowed_image_src_prefixes())
    return False


def _allow_iframe_attribute(tag, name, value):
    if name == "src":
        return bool(YOUTUBE_EMBED_RE.match(value) or VIMEO_EMBED_RE.match(value))
    if name in ("frameborder", "allowfullscreen", "class"):
        return True
    return False


def _allow_span_attribute(tag, name, value):
    if name != "class":
        return False
    return set(value.split()) <= ALLOWED_SPAN_CLASSES


ALLOWED_BODY_ATTRIBUTES = {
    "a": ["href", "rel", "target"],
    "img": _allow_img_attribute,
    "iframe": _allow_iframe_attribute,
    "span": _allow_span_attribute,
}
ALLOWED_BODY_PROTOCOLS = ["http", "https", "mailto"]


def is_rate_limited(user, model_class, field_name="author"):
    limit = RATE_LIMIT_MAX.get(model_class._meta.model_name)
    if limit is None:
        return False
    window_start = timezone.now() - RATE_LIMIT_WINDOW
    return (
        model_class.objects.filter(**{field_name: user}, created_at__gte=window_start).count()
        >= limit
    )


IFRAME_OPEN_TAG_RE = re.compile(r"<iframe\b([^>]*)>")


IFRAME_SANDBOX = "allow-scripts allow-same-origin allow-presentation"


def _lock_down_iframe(match):
    attrs = match.group(1)
    return f'<iframe{attrs} sandbox="{IFRAME_SANDBOX}" referrerpolicy="no-referrer">'


def sanitize_post_body(raw_html):
    cleaned = bleach.clean(
        raw_html or "",
        tags=ALLOWED_BODY_TAGS,
        attributes=ALLOWED_BODY_ATTRIBUTES,
        protocols=ALLOWED_BODY_PROTOCOLS,
        strip=True,
    )
    cleaned = IFRAME_OPEN_TAG_RE.sub(_lock_down_iframe, cleaned)
    return bleach.linkify(
        cleaned, callbacks=[bleach.callbacks.nofollow, bleach.callbacks.target_blank]
    )
