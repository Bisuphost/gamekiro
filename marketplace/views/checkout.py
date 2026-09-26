from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from marketplace import providers
from marketplace.models import Order
from marketplace.providers import mock as mock_provider
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import coupons as coupons_service
from marketplace.services import payments as payments_service
from marketplace.services import pricing as pricing_service
from marketplace.services import ratelimit as ratelimit_service

from .decorators import require_marketplace_enabled, require_verified_email
from .utils import client_ip

CHECKOUT_RATE_LIMIT = 5
CHECKOUT_RATE_WINDOW_SECONDS = 60
CHECKOUT_IP_RATE_LIMIT = 20


@login_required
@require_marketplace_enabled
@require_verified_email
def checkout_review(request):
    cart = cart_service.get_or_create_cart(request.user)
    for problem in cart_service.validate_cart(cart):
        messages.warning(request, problem)

    coupon_code = request.session.get("marketplace_coupon_code")
    coupon = None
    if coupon_code:
        raw_subtotal = sum(
            item.product.unit_price_minor * item.quantity
            for item in cart.items.select_related("product")
        )
        try:
            coupon = coupons_service.validate_coupon(coupon_code, request.user, raw_subtotal)
        except coupons_service.CouponInvalid:
            request.session.pop("marketplace_coupon_code", None)

    priced = pricing_service.price_cart(cart, coupon=coupon)
    return render(
        request,
        "marketplace/checkout.html",
        {
            "cart": cart,
            "priced": priced,
            "coupon": coupon,
            "payment_providers": providers.available_providers(),
        },
    )


@login_required
@require_marketplace_enabled
@require_verified_email
@require_POST
def checkout_confirm(request):
    if not ratelimit_service.hit(
        "checkout_confirm", request.user.pk, CHECKOUT_RATE_LIMIT, CHECKOUT_RATE_WINDOW_SECONDS
    ):
        messages.error(request, "Too many checkout attempts. Please wait a moment and try again.")
        return redirect("marketplace:checkout")
    if not ratelimit_service.hit(
        "checkout_confirm_ip",
        client_ip(request),
        CHECKOUT_IP_RATE_LIMIT,
        CHECKOUT_RATE_WINDOW_SECONDS,
    ):
        messages.error(request, "Too many checkout attempts. Please wait a moment and try again.")
        return redirect("marketplace:checkout")

    coupon_code = request.session.get("marketplace_coupon_code")
    try:
        order, payment, intent = checkout_service.create_order(
            request.user,
            provider_slug=request.POST.get("provider", "").strip(),
            coupon_code=coupon_code,
            ip=client_ip(request),
        )
    except checkout_service.CheckoutError as exc:
        messages.error(request, str(exc))
        return redirect("marketplace:checkout")

    request.session.pop("marketplace_coupon_code", None)

    if intent is None:
        messages.error(
            request,
            "We couldn't reach the payment provider. Your items are reserved for a short "
            "time; choose Pay now to try again.",
        )
        return redirect("marketplace:order_detail", reference=order.reference)
    return redirect(intent.redirect_url)


@login_required
@require_marketplace_enabled
@require_verified_email
@require_POST
def order_pay(request, reference):
    order = get_object_or_404(Order, reference=reference, user=request.user)
    if not ratelimit_service.hit(
        "order_pay", request.user.pk, CHECKOUT_RATE_LIMIT, CHECKOUT_RATE_WINDOW_SECONDS
    ):
        messages.error(request, "Too many attempts. Please wait a moment and try again.")
        return redirect("marketplace:order_detail", reference=order.reference)
    if not ratelimit_service.hit(
        "order_pay_ip", client_ip(request), CHECKOUT_IP_RATE_LIMIT, CHECKOUT_RATE_WINDOW_SECONDS
    ):
        messages.error(request, "Too many attempts. Please wait a moment and try again.")
        return redirect("marketplace:order_detail", reference=order.reference)

    try:
        _, intent = payments_service.start_or_resume_payment(
            order, request.POST.get("provider", "").strip(), ip=client_ip(request)
        )
    except payments_service.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("marketplace:order_detail", reference=order.reference)

    if intent is None:
        messages.error(
            request, "We couldn't reach the payment provider. Please try again in a moment."
        )
        return redirect("marketplace:order_detail", reference=order.reference)
    return redirect(intent.redirect_url)


@login_required
def mock_checkout_page(request, reference):
    if not providers.mock_allowed():
        raise Http404
    order = get_object_or_404(Order, reference=reference, user=request.user)

    if request.method == "POST":
        outcome = request.POST.get("outcome", "succeed")
        event_type = "payment_succeeded" if outcome == "succeed" else "payment_failed"
        raw_body, signature = mock_provider.build_signed_event(
            event_type, order, order.total_minor, order.currency
        )
        from marketplace.services import webhooks as webhooks_service

        webhooks_service.handle("mock", raw_body, {"X-Mock-Signature": signature})
        return redirect("marketplace:order_detail", reference=order.reference)

    return render(request, "marketplace/mock_checkout.html", {"order": order})
