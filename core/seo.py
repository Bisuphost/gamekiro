from django.conf import settings


def absolute_media_url(url):
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return settings.SITE_URL.rstrip("/") + url


def absolute_url(path):
    return settings.SITE_URL.rstrip("/") + path
