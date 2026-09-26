from django.conf import settings
from django.utils import timezone

from marketplace.models import Order, Payment

from . import ratelimit as ratelimit_service

ORDER_VELOCITY_WINDOW_SECONDS = 24 * 60 * 60
FAILURE_LOCKOUT_ACTION = "payment_failure"


class FraudBlock(Exception):
    pass


def check_order_velocity(user):
    limit = settings.MARKETPLACE_MAX_ORDERS_PER_DAY
    if limit <= 0:
        return
    if not ratelimit_service.hit("order_velocity", user.pk, limit, ORDER_VELOCITY_WINDOW_SECONDS):
        raise FraudBlock(
            "You've reached the daily limit for new orders. Please try again tomorrow or "
            "contact support."
        )


def _lockout_identifiers(user, ip):
    identifiers = []
    if user is not None and getattr(user, "pk", None):
        identifiers.append(f"user:{user.pk}")
    if ip:
        identifiers.append(f"ip:{ip}")
    return identifiers


def check_payment_failure_lockout(user, ip):
    limit = settings.MARKETPLACE_MAX_PAYMENT_FAILURES
    if limit <= 0:
        return
    window = settings.MARKETPLACE_PAYMENT_FAILURE_COOLDOWN_MINUTES * 60
    for identifier in _lockout_identifiers(user, ip):
        if ratelimit_service.peek(FAILURE_LOCKOUT_ACTION, identifier, window) >= limit:
            raise FraudBlock(
                "Too many failed payments recently. Please wait before trying again, or "
                "contact support if you need help."
            )


def record_payment_failure(payment, user):
    limit = settings.MARKETPLACE_MAX_PAYMENT_FAILURES
    if limit <= 0:
        return
    window = settings.MARKETPLACE_PAYMENT_FAILURE_COOLDOWN_MINUTES * 60
    for identifier in _lockout_identifiers(user, payment.created_ip):
        ratelimit_service.hit(FAILURE_LOCKOUT_ACTION, identifier, limit, window)


def _has_prior_paid_order(user, excluding_order_id):
    settled_statuses = [
        Order.Status.PAID,
        Order.Status.PAID_LATE,
        Order.Status.PARTIALLY_FULFILLED,
        Order.Status.FULFILLED,
    ]
    return (
        Order.objects.filter(user=user, status__in=settled_statuses)
        .exclude(pk=excluding_order_id)
        .exists()
    )


def review_reason(order):
    high_value_threshold = settings.MARKETPLACE_HIGH_VALUE_REVIEW_MINOR
    if high_value_threshold > 0 and order.total_minor >= high_value_threshold:
        return "High-value order held for manual review before fulfilment."

    first_order_threshold = settings.MARKETPLACE_FIRST_ORDER_REVIEW_MINOR
    if first_order_threshold > 0 and order.total_minor >= first_order_threshold:
        if not _has_prior_paid_order(order.user, order.pk):
            return "First order at this value held for manual review before fulfilment."

    return None


def record_created_ip(payment, ip):
    if ip:
        Payment.objects.filter(pk=payment.pk).update(created_ip=ip, updated_at=timezone.now())
