from django.conf import settings

from .mock import MockProvider
from .paypal_provider import PayPalProvider
from .stripe_provider import StripeProvider

PROVIDERS = {
    "mock": MockProvider(),
    "stripe": StripeProvider(),
    "paypal": PayPalProvider(),
}


def mock_allowed():
    return bool(getattr(settings, "MARKETPLACE_ALLOW_MOCK", False))


def get_provider(slug):
    if slug == "mock" and not mock_allowed():
        raise ValueError("The mock payment provider is disabled.")
    try:
        return PROVIDERS[slug]
    except KeyError as exc:
        raise ValueError(f"Unknown payment provider: {slug!r}") from exc


def available_providers():
    from marketplace.services import flags as flags_service

    available = []
    for slug in settings.PAYMENT_PROVIDERS_ENABLED:
        try:
            provider = get_provider(slug)
        except ValueError:
            continue
        if provider.is_configured() and flags_service.provider_enabled(slug):
            available.append(provider)
    return available
