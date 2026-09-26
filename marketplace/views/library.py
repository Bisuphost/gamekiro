from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from marketplace.models import Entitlement, KeyAccessLog
from marketplace.services import inventory as inventory_service
from marketplace.services import keys as keys_service
from marketplace.services import ratelimit as ratelimit_service

from .decorators import require_marketplace_enabled
from .utils import client_ip

KEY_REVEAL_RATE_LIMIT = 20
KEY_REVEAL_RATE_WINDOW_SECONDS = 3600


@login_required
@require_marketplace_enabled
def library_list(request):
    entitlements = (
        Entitlement.objects.filter(user=request.user, revoked_at__isnull=True)
        .select_related("product__game", "order_item__order")
        .order_by("-granted_at")
    )
    return render(request, "marketplace/library.html", {"entitlements": entitlements})


@login_required
@require_marketplace_enabled
@require_POST
def reveal_key(request, entitlement_id):
    entitlement = get_object_or_404(
        Entitlement.objects.select_related("order_item__order", "product"),
        pk=entitlement_id,
        user=request.user,
        revoked_at__isnull=True,
    )
    order = entitlement.order_item.order

    if order.user_id != request.user.id:
        raise Http404

    if entitlement.order_item.delivered_at is None:
        return render(
            request,
            "marketplace/_key_reveal.html",
            {"entitlement": entitlement, "error": "This key is not ready to be revealed yet."},
        )

    key = getattr(entitlement.order_item, "reserved_key", None)
    if key is None:
        raise Http404

    ip_address = client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    if not ratelimit_service.hit(
        "key_reveal", request.user.pk, KEY_REVEAL_RATE_LIMIT, KEY_REVEAL_RATE_WINDOW_SECONDS
    ):
        keys_service.record_access(
            request.user, key, order, KeyAccessLog.Action.RATE_LIMITED, ip_address, user_agent
        )
        return render(
            request,
            "marketplace/_key_reveal.html",
            {"entitlement": entitlement, "error": "Too many reveal attempts. Try again later."},
        )

    plaintext_key = keys_service.decrypt(key.encrypted_key)
    inventory_service.mark_key_delivered(key)
    keys_service.record_access(
        request.user, key, order, KeyAccessLog.Action.REVEAL, ip_address, user_agent
    )

    return render(
        request,
        "marketplace/_key_reveal.html",
        {"entitlement": entitlement, "plaintext_key": plaintext_key},
    )
