from django.conf import settings


def client_ip(request):
    """Return the caller's IP, resistant to a client-supplied X-Forwarded-For.

    ``X-Forwarded-For`` is attacker-controlled unless a trusted reverse proxy
    overwrites it, so by default (``MARKETPLACE_TRUSTED_PROXY_COUNT=0``) we
    trust only ``REMOTE_ADDR`` — the actual TCP peer, which a client cannot
    spoof. If the app sits behind N trusted proxies that each append to
    X-Forwarded-For (never trusting what the client itself sent), set that
    count so the Nth-from-the-right entry — the one the outermost trusted
    proxy recorded — is used instead.
    """
    trusted_hops = getattr(settings, "MARKETPLACE_TRUSTED_PROXY_COUNT", 0)
    if trusted_hops > 0:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded_for:
            hops = [part.strip() for part in forwarded_for.split(",") if part.strip()]
            if len(hops) >= trusted_hops:
                return hops[-trusted_hops]
    return request.META.get("REMOTE_ADDR")
