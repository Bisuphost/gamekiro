from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from marketplace.models import Product
from marketplace.services import cart as cart_service
from marketplace.services import coupons as coupons_service
from marketplace.services import pricing as pricing_service

from .decorators import require_marketplace_enabled


def _redirect_back(request, fallback="marketplace:cart"):
    for candidate in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(candidate)
    return redirect(fallback)


def _cart_context(request):
    cart = cart_service.get_or_create_cart(request.user)
    coupon_code = request.session.get("marketplace_coupon_code")
    coupon = None
    if coupon_code:
        try:
            raw_subtotal = sum(
                item.product.unit_price_minor * item.quantity
                for item in cart.items.select_related("product")
            )
            coupon = coupons_service.validate_coupon(coupon_code, request.user, raw_subtotal)
        except coupons_service.CouponInvalid:
            request.session.pop("marketplace_coupon_code", None)
    priced = pricing_service.price_cart(cart, coupon=coupon)
    return {
        "cart": cart,
        "priced": priced,
        "coupon": coupon,
        "cart_item_count": cart_service.item_count(request.user),
    }


@login_required
@require_marketplace_enabled
def view_cart(request):
    context = _cart_context(request)
    return render(request, "marketplace/cart.html", context)


@login_required
def cart_count(request):
    return render(
        request,
        "marketplace/_cart_count.html",
        {"cart_item_count": cart_service.item_count(request.user)},
    )


@login_required
@require_marketplace_enabled
@require_POST
def add_to_cart(request):
    product = get_object_or_404(Product, pk=request.POST.get("product_id"))
    quantity = int(request.POST.get("quantity", 1) or 1)
    try:
        cart_service.add_item(request.user, product, quantity=quantity)
    except cart_service.CartError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Added {product} to your cart.")

    if request.htmx:
        return render(request, "marketplace/_cart_summary_htmx.html", _cart_context(request))
    return _redirect_back(request)


@login_required
@require_marketplace_enabled
@require_POST
def update_cart(request):
    product_id = request.POST.get("product_id")
    quantity = int(request.POST.get("quantity", 1) or 0)
    try:
        cart_service.update_quantity(request.user, product_id, quantity)
    except cart_service.CartError as exc:
        messages.error(request, str(exc))

    if request.htmx:
        return render(request, "marketplace/_cart_summary_htmx.html", _cart_context(request))
    return redirect("marketplace:cart")


@login_required
@require_marketplace_enabled
@require_POST
def remove_from_cart(request):
    product_id = request.POST.get("product_id")
    cart_service.remove_item(request.user, product_id)

    if request.htmx:
        return render(request, "marketplace/_cart_summary_htmx.html", _cart_context(request))
    return redirect("marketplace:cart")


@login_required
@require_marketplace_enabled
@require_POST
def validate_coupon_view(request):
    code = request.POST.get("code", "").strip()
    cart = cart_service.get_or_create_cart(request.user)
    raw_subtotal = sum(
        item.product.unit_price_minor * item.quantity
        for item in cart.items.select_related("product")
    )
    try:
        coupons_service.validate_coupon(code, request.user, raw_subtotal)
    except coupons_service.CouponInvalid as exc:
        messages.error(request, str(exc))
        request.session.pop("marketplace_coupon_code", None)
    else:
        request.session["marketplace_coupon_code"] = code
        messages.success(request, "Coupon applied.")

    if request.htmx:
        return render(request, "marketplace/_cart_summary_htmx.html", _cart_context(request))
    return redirect("marketplace:cart")
