import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from marketplace import providers
from marketplace.models import Order
from marketplace.providers.base import ProviderError, WebhookVerificationError
from marketplace.services import payments as payments_service
from marketplace.services import ratelimit as ratelimit_service

from .decorators import require_marketplace_enabled

logger = logging.getLogger("marketplace")

PULL_LIMIT = 1
PULL_WINDOW_SECONDS = 10
RETURN_PARAMS = ("session_id", "token")


def _refresh_from_provider(order, params=None):
    try:
        if params is not None:
            payments_service.reconcile_return(order, params)
        else:
            payments_service.reconcile_order(order)
    except (ProviderError, WebhookVerificationError, ValueError):
        logger.exception("Payment reconciliation failed for order %s", order.reference)
    order.refresh_from_db()


@login_required
@require_marketplace_enabled
def order_list(request):
    orders = Order.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "marketplace/orders.html", {"orders": orders})


@login_required
@require_marketplace_enabled
def order_detail(request, reference):
    order = get_object_or_404(
        Order.objects.select_related("coupon"), reference=reference, user=request.user
    )
    if order.status == Order.Status.PENDING_PAYMENT and any(
        request.GET.get(name) for name in RETURN_PARAMS
    ):
        _refresh_from_provider(order, request.GET)
    items = order.items.select_related("product", "product__game").all()
    return render(
        request,
        "marketplace/order_detail.html",
        {
            "order": order,
            "items": items,
            "payment_providers": providers.available_providers(),
        },
    )


@login_required
@require_marketplace_enabled
def check_payment_status(request, reference):
    order = get_object_or_404(Order, reference=reference, user=request.user)

    if order.status == Order.Status.PENDING_PAYMENT and ratelimit_service.hit(
        "order_pull", order.pk, PULL_LIMIT, PULL_WINDOW_SECONDS
    ):
        _refresh_from_provider(order)

    if request.htmx:
        return render(request, "marketplace/_order_status.html", {"order": order})
    return redirect("marketplace:order_detail", reference=order.reference)
