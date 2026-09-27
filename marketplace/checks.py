from django.conf import settings
from django.core.checks import Error, register


def _is_production():
    # Django's test runner forces settings.DEBUG = False for the duration of
    # every test run, regardless of what the settings module actually sets —
    # so DEBUG alone can't tell "really in production" apart from "running
    # under manage.py test with dev settings". SETTINGS_MODULE isn't affected
    # by that override, so checks that must stay silent in dev/CI need it too.
    # override_settings() itself reports SETTINGS_MODULE as None when a test
    # doesn't explicitly override it, so this must tolerate that as well.
    settings_module = settings.SETTINGS_MODULE or ""
    return not settings.DEBUG and not settings_module.endswith(".dev")


@register()
def check_production_database_backend(app_configs, **kwargs):
    errors = []
    if not _is_production():
        return errors
    if getattr(settings, "MARKETPLACE_ALLOW_SQLITE", False):
        return errors
    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    if engine.endswith("sqlite3"):
        errors.append(
            Error(
                "The marketplace requires PostgreSQL (or MySQL/MariaDB) in production.",
                hint=(
                    "SQLite's select_for_update() is a silent no-op and it serialises "
                    "all site writes, which is unsafe for concurrent key allocation and "
                    "payment webhooks. Set DATABASE_URL to a PostgreSQL connection, or "
                    "set MARKETPLACE_ALLOW_SQLITE=True to override at your own risk."
                ),
                id="marketplace.E001",
            )
        )
    return errors


@register()
def check_mock_provider_not_in_production(app_configs, **kwargs):
    if not _is_production() or not getattr(settings, "MARKETPLACE_ALLOW_MOCK", False):
        return []
    return [
        Error(
            "The mock payment provider is enabled outside development.",
            hint=(
                "The mock provider lets any signed-in user mark their own order as paid. "
                "Set MARKETPLACE_ALLOW_MOCK = False."
            ),
            id="marketplace.E002",
        )
    ]


@register()
def check_stripe_configuration(app_configs, **kwargs):
    if "stripe" not in settings.PAYMENT_PROVIDERS_ENABLED:
        return []
    errors = []
    key = settings.STRIPE_SECRET_KEY or ""
    if not key or not settings.STRIPE_WEBHOOK_SECRETS:
        errors.append(
            Error(
                "Stripe is enabled but STRIPE_SECRET_KEY or STRIPE_WEBHOOK_SECRETS is missing.",
                hint="Set both, or remove 'stripe' from PAYMENT_PROVIDERS_ENABLED.",
                id="marketplace.E003",
            )
        )
        return errors
    if not key.startswith(("sk_", "rk_")):
        errors.append(
            Error(
                "STRIPE_SECRET_KEY must be a secret (sk_...) or restricted (rk_...) key.",
                hint="Never put a publishable key (pk_...) here.",
                id="marketplace.E004",
            )
        )
    is_test_key = key.startswith(("sk_test_", "rk_test_"))
    if _is_production() and is_test_key:
        errors.append(
            Error(
                "A Stripe test-mode key is configured in production.",
                hint="Use the live-mode key, or disable Stripe until you go live.",
                id="marketplace.E005",
            )
        )
    if not _is_production() and key.startswith(("sk_live_", "rk_live_")):
        errors.append(
            Error(
                "A live Stripe key is configured outside production.",
                hint="Use a test-mode key for development and CI.",
                id="marketplace.E006",
            )
        )
    if _is_production() and not settings.SITE_URL.startswith("https://"):
        errors.append(
            Error(
                "SITE_URL must be an https:// URL when Stripe is enabled.",
                hint="Stripe redirects and webhooks require HTTPS in live mode.",
                id="marketplace.E007",
            )
        )
    if settings.MARKETPLACE_RESERVATION_TTL_MINUTES < 35:
        errors.append(
            Error(
                "MARKETPLACE_RESERVATION_TTL_MINUTES must be at least 35 when Stripe is enabled.",
                hint=(
                    "Stripe Checkout sessions last at least 30 minutes; the reservation must "
                    "outlive the session or a late payment could find no stock."
                ),
                id="marketplace.E008",
            )
        )
    return errors


@register()
def check_paypal_configuration(app_configs, **kwargs):
    if "paypal" not in settings.PAYMENT_PROVIDERS_ENABLED:
        return []
    errors = []
    if not (
        settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET and settings.PAYPAL_WEBHOOK_ID
    ):
        errors.append(
            Error(
                "PayPal is enabled but PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET or "
                "PAYPAL_WEBHOOK_ID is missing.",
                hint="Set all three, or remove 'paypal' from PAYMENT_PROVIDERS_ENABLED.",
                id="marketplace.E009",
            )
        )
        return errors
    if settings.PAYPAL_ENV not in ("sandbox", "live"):
        errors.append(
            Error(
                "PAYPAL_ENV must be 'sandbox' or 'live'.",
                id="marketplace.E010",
            )
        )
    if _is_production() and settings.PAYPAL_ENV != "live":
        errors.append(
            Error(
                "PayPal is running against the sandbox in production.",
                hint="Set PAYPAL_ENV=live with live credentials, or disable PayPal until launch.",
                id="marketplace.E011",
            )
        )
    if not _is_production() and settings.PAYPAL_ENV == "live":
        errors.append(
            Error(
                "PayPal live mode is configured outside production.",
                hint="Use PAYPAL_ENV=sandbox for development and CI.",
                id="marketplace.E012",
            )
        )
    if _is_production() and not settings.SITE_URL.startswith("https://"):
        errors.append(
            Error(
                "SITE_URL must be an https:// URL when PayPal is enabled.",
                id="marketplace.E013",
            )
        )
    return errors


@register()
def check_fraud_control_configuration(app_configs, **kwargs):
    errors = []
    if (
        _is_production()
        and settings.MARKETPLACE_REQUIRE_VERIFIED_EMAIL
        and not settings.SITE_URL.startswith("https://")
    ):
        errors.append(
            Error(
                "SITE_URL must be an https:// URL when email verification is required.",
                hint="Verification links are emailed to buyers and must be HTTPS.",
                id="marketplace.E014",
            )
        )
    if _is_production() and settings.MARKETPLACE_TRUSTED_PROXY_COUNT < 0:
        errors.append(
            Error(
                "MARKETPLACE_TRUSTED_PROXY_COUNT must not be negative.",
                id="marketplace.E015",
            )
        )
    return errors
