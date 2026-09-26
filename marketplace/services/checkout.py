from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from marketplace import providers
from marketplace.models import Order, OrderItem, Payment

from . import cart as cart_service
from . import coupons as coupons_service
from . import flags as flags_service
from . import fraud as fraud_service
from . import inventory as inventory_service
from . import payments as payments_service
from . import pricing as pricing_service


class CheckoutError(Exception):
    pass


def resolve_provider(provider_slug):
    available = providers.available_providers()
    if not available:
        raise CheckoutError("No payment method is available right now. Please try again later.")
    if not provider_slug:
        if len(available) == 1:
            return available[0]
        raise CheckoutError("Please choose a payment method.")
    for provider in available:
        if provider.slug == provider_slug:
            return provider
    raise CheckoutError("That payment method is not available right now.")


def create_order(user, provider_slug=None, coupon_code=None, ttl_minutes=None, ip=None):
    if not flags_service.purchases_enabled():
        raise CheckoutError("Purchases are currently disabled.")
    try:
        fraud_service.check_payment_failure_lockout(user, ip)
        fraud_service.check_order_velocity(user)
    except fraud_service.FraudBlock as exc:
        raise CheckoutError(str(exc)) from exc
    provider = resolve_provider(provider_slug)

    with transaction.atomic():
        cart = cart_service.get_or_create_cart(user)

        for item in cart.items.select_related("product"):
            inventory_service.sweep_expired_reservations_for_product(item.product)

        cart_service.validate_cart(cart)
        items = list(cart.items.select_related("product__activation_platform", "product__region"))
        if not items:
            raise CheckoutError("Your cart is empty or no longer has purchasable items.")

        product_ids = [item.product_id for item in items]
        raw_subtotal = sum(item.product.unit_price_minor * item.quantity for item in items)

        coupon = None
        if coupon_code:
            try:
                coupon = coupons_service.validate_coupon(
                    coupon_code, user, raw_subtotal, product_ids=product_ids
                )
            except coupons_service.CouponInvalid as exc:
                raise CheckoutError(str(exc)) from exc

        pricing_lines = [(item.product, item.quantity) for item in items]
        priced = pricing_service.price_line_items(pricing_lines, coupon=coupon)

        if priced["total_minor"] < provider.min_amount_minor:
            raise CheckoutError(
                "The order total is below the minimum amount for this payment method."
            )

        now = timezone.now()
        ttl_minutes = ttl_minutes or settings.MARKETPLACE_RESERVATION_TTL_MINUTES
        order = Order.objects.create(
            user=user,
            subtotal_minor=priced["subtotal_minor"],
            discount_minor=priced["discount_minor"],
            tax_minor=priced["tax_minor"],
            total_minor=priced["total_minor"],
            currency=priced["currency"],
            coupon=coupon,
            reservation_expires_at=now + timedelta(minutes=ttl_minutes),
        )

        order_item_objs = []
        for line in priced["lines"]:
            product = line["product"]
            for _ in range(line["quantity"]):
                order_item_objs.append(
                    OrderItem(
                        order=order,
                        product=product,
                        unit_price_minor=line["unit_price_minor"],
                        product_title=str(product),
                        platform_name=product.activation_platform.name,
                        region_name=product.region.name,
                    )
                )
        OrderItem.objects.bulk_create(order_item_objs)

        try:
            inventory_service.reserve_keys_for_order(order, ttl_minutes=ttl_minutes, now=now)
        except inventory_service.OutOfStock as exc:
            raise CheckoutError(
                "One or more items in your cart just sold out. Please review your cart."
            ) from exc

        if coupon is not None:
            coupons_service.redeem_coupon(coupon, user, order, priced["discount_minor"])

        payment = Payment.objects.create(
            order=order,
            provider=provider.slug,
            status=Payment.Status.CREATED,
            amount_minor=order.total_minor,
            currency=order.currency,
            attempt=1,
            idempotency_key=payments_service.attempt_key(order, provider.slug, 1),
        )

        cart.items.all().delete()

    fraud_service.record_created_ip(payment, ip)
    intent = payments_service.begin_payment(order, payment)
    return order, payment, intent
