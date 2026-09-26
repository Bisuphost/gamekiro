SCRUB_HEADERS = {
    "authorization",
    "cookie",
    "stripe-signature",
    "paypal-transmission-sig",
    "paypal-transmission-id",
    "paypal-auth-algo",
    "paypal-cert-url",
}

SCRUB_KEYS = {
    "stripe_secret_key",
    "stripe_webhook_secrets",
    "paypal_client_secret",
    "paypal_webhook_id",
    "django_secret_key",
    "marketplace_key_enc_key",
}


def scrub_sentry_event(event, hint):
    request = event.get("request")
    if request:
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in SCRUB_HEADERS:
                    headers[name] = "[Filtered]"
        for section in ("data", "query_string"):
            value = request.get(section)
            if isinstance(value, dict):
                for key in list(value):
                    if key.lower() in SCRUB_KEYS:
                        value[key] = "[Filtered]"
    return event
