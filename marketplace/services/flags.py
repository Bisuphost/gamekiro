from django.conf import settings
from django.core.cache import cache

from marketplace.models import MarketplaceSettings

CACHE_KEY = "marketplace:settings-singleton"
CACHE_TTL_SECONDS = 5


def _load_settings():
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    settings_row = MarketplaceSettings.load()
    cache.set(CACHE_KEY, settings_row, CACHE_TTL_SECONDS)
    return settings_row


def invalidate():
    cache.delete(CACHE_KEY)


def marketplace_enabled():
    if not settings.MARKETPLACE_ENABLED:
        return False
    return _load_settings().marketplace_enabled


def purchases_enabled():
    if not marketplace_enabled():
        return False
    return _load_settings().purchases_enabled


def fulfillment_enabled():
    if not marketplace_enabled():
        return False
    return _load_settings().fulfillment_enabled


def provider_enabled(slug):
    if slug == "mock":
        return True
    if not purchases_enabled():
        return False
    return bool(getattr(_load_settings(), f"{slug}_enabled", False))


def maintenance_message():
    return _load_settings().maintenance_message
