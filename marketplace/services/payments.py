import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from marketplace import providers
from marketplace.models import FulfillmentJob, Order, OrderAuditLog, Payment, order_transition
from marketplace.providers.base import PaymentIntent, PaymentUrls, ProviderError

from . import disputes as disputes_service
from . import fraud as fraud_service
from . import inventory as inventory_service
from . import notifications as notifications_service
from . import refunds as refunds_service

logger = logging.getLogger("marketplace")

LATE_PAYMENT_WINDOW_HOURS = 72
HANDLED_EVENT_TYPES = {
    "payment_succeeded",
    "payment_failed",
    "dispute_created",
    "attempt_expired",
    "attempt_failed",
    "buyer_approved",
    "refund_updated",
    "dispute_updated",
    "dispute_closed",
}
OPEN_PAYMENT_STATUSES = [Payment.Status.CREATED, Payment.Status.PENDING]
UNSETTLED_PAYMENT_STATUSES = OPEN_PAYMENT_STATUSES + [
    Payment.Status.CANCELLED,
    Payment.Status.EXPIRED,
    Payment.Status.FAILED,
]


class PaymentError(Exception):
    pass


def payment_urls(order):
    base = settings.SITE_URL.rstrip("/")
    order_url = base + reverse("marketplace:order_detail", args=[order.reference])
    return PaymentUrls(success_url=order_url, cancel_url=order_url)


def attempt_key(order, provider_slug, attempt):
    return f"{order.reference}:{provider_slug}:{attempt}"


def begin_payment(order, payment):
    provider = providers.get_provider(payment.provider)
    try:
        intent = provider.create_payment(order, payment, payment_urls(order))
    except ProviderError as exc:
        logger.warning(
            "Provider %s could not create a payment for %s: %s",
            payment.provider,
            order.reference,
            exc,
        )
        Payment.objects.filter(pk=payment.pk).update(
            failure_code=(exc.code or "provider_error")[:64],
            status=Payment.Status.FAILED if exc.definitive else payment.status,
            updated_at=timezone.now(),
        )
        return None
    now = timezone.now()
    Payment.objects.filter(pk=payment.pk).update(
        provider_payment_id=intent.provider_payment_id,
        provider_reference=intent.provider_reference,
        checkout_url=intent.redirect_url,
        expires_at=intent.expires_at,
        failure_code="",
        updated_at=now,
    )
    Order.objects.filter(pk=order.pk).update(
        provider=provider.slug, provider_session_id=intent.provider_payment_id, updated_at=now
    )
    return intent


def start_or_resume_payment(order, provider_slug, ip=None):
    now = timezone.now()
    order = Order.objects.get(pk=order.pk)
    if order.status != Order.Status.PENDING_PAYMENT:
        raise PaymentError("This order can no longer be paid.")
    if order.reservation_expires_at and order.reservation_expires_at <= now:
        raise PaymentError("Your reservation has expired. Please start a new order.")
    try:
        fraud_service.check_payment_failure_lockout(order.user, ip)
    except fraud_service.FraudBlock as exc:
        raise PaymentError(str(exc)) from exc

    provider = next((p for p in providers.available_providers() if p.slug == provider_slug), None)
    if provider is None:
        raise PaymentError("That payment method is not available right now.")
    if order.total_minor < provider.min_amount_minor:
        raise PaymentError("This order is below the minimum for that payment method.")

    attempts = list(order.payments.order_by("-attempt"))
    if any(a.status == Payment.Status.PENDING for a in attempts):
        raise PaymentError("A payment for this order is already being processed.")

    latest = attempts[0] if attempts else None
    if latest and latest.provider == provider.slug and latest.status == Payment.Status.CREATED:
        if not latest.provider_payment_id:
            intent = begin_payment(order, latest)
            return latest, intent
        still_valid = latest.expires_at is None or latest.expires_at > now
        if latest.checkout_url and still_valid:
            resumed = PaymentIntent(
                provider_payment_id=latest.provider_payment_id,
                redirect_url=latest.checkout_url,
                provider_reference=latest.provider_reference,
                expires_at=latest.expires_at,
            )
            return latest, resumed

    if provider.min_session_minutes and order.reservation_expires_at:
        needed = now + timedelta(minutes=provider.min_session_minutes)
        if order.reservation_expires_at < needed:
            raise PaymentError(
                "Your reservation is too close to expiring to start a new payment. "
                "Please start a new order."
            )

    for previous in attempts:
        if previous.status != Payment.Status.CREATED or not previous.provider_payment_id:
            continue
        try:
            providers.get_provider(previous.provider).cancel_payment(previous)
        except ProviderError as exc:
            raise PaymentError("We couldn't safely replace your previous payment attempt.") from exc
        Payment.objects.filter(pk=previous.pk, status=Payment.Status.CREATED).update(
            status=Payment.Status.CANCELLED, updated_at=timezone.now()
        )

    next_attempt = (latest.attempt if latest else 0) + 1
    try:
        with transaction.atomic():
            payment = Payment.objects.create(
                order=order,
                provider=provider.slug,
                status=Payment.Status.CREATED,
                amount_minor=order.total_minor,
                currency=order.currency,
                attempt=next_attempt,
                idempotency_key=attempt_key(order, provider.slug, next_attempt),
            )
    except IntegrityError as exc:
        raise PaymentError("Another payment attempt is in progress. Please try again.") from exc
    fraud_service.record_created_ip(payment, ip)
    return payment, begin_payment(order, payment)


def _resolve_order(event):
    if event.order_reference:
        return Order.objects.filter(reference=event.order_reference).first()
    if event.provider in ("", "mock"):
        return None
    identifiers = [i for i in (event.provider_payment_id, event.provider_reference) if i]
    if not identifiers:
        return None
    payment = (
        Payment.objects.filter(provider=event.provider)
        .filter(Q(provider_payment_id__in=identifiers) | Q(provider_reference__in=identifiers))
        .select_related("order")
        .first()
    )
    return payment.order if payment else None


def _bound_payments(order, event):
    payments = Payment.objects.filter(order=order)
    if event.provider in ("", "mock"):
        return payments.filter(provider=event.provider or "mock")
    identifiers = [i for i in (event.provider_payment_id, event.provider_reference) if i]
    if not identifiers:
        return payments.none()
    return payments.filter(provider=event.provider).filter(
        Q(provider_payment_id__in=identifiers) | Q(provider_reference__in=identifiers)
    )


def _event_is_bound(order, event):
    if event.provider in ("", "mock"):
        return True
    return _bound_payments(order, event).exists()


def _mark_payment_paid(order, event, now):
    payments = _bound_payments(order, event)
    if event.provider in ("", "mock"):
        latest = payments.filter(status__in=OPEN_PAYMENT_STATUSES).order_by("-attempt").first()
        payments = Payment.objects.filter(pk=latest.pk) if latest else Payment.objects.none()
    else:
        payments = payments.filter(status__in=UNSETTLED_PAYMENT_STATUSES)
    fields = {"status": Payment.Status.PAID, "confirmed_at": now, "updated_at": now}
    if event.provider_reference:
        fields["provider_reference"] = event.provider_reference
    return payments.update(**fields)


def _apply_attempt_end(order, event, now):
    is_decline = event.event_type == "attempt_failed"
    target = Payment.Status.FAILED if is_decline else Payment.Status.EXPIRED
    open_payments = list(_bound_payments(order, event).filter(status__in=OPEN_PAYMENT_STATUSES))
    if not open_payments:
        return "duplicate_transition", order
    Payment.objects.filter(pk__in=[p.pk for p in open_payments]).update(
        status=target, updated_at=now
    )
    _record(order, event.event_type, {"provider": event.provider})
    if is_decline:
        for payment in open_payments:
            fraud_service.record_payment_failure(payment, order.user)
    return "processed", order


CAPTURE_CLAIM_STALE_SECONDS = 60


def capture_guarded(order, payment, provider):
    now = timezone.now()
    order = Order.objects.get(pk=order.pk)
    if order.status != Order.Status.PENDING_PAYMENT:
        return "not_captured", order
    if order.reservation_expires_at and order.reservation_expires_at <= now:
        return "not_captured", order

    stale_before = now - timedelta(seconds=CAPTURE_CLAIM_STALE_SECONDS)
    claimed = (
        Payment.objects.filter(pk=payment.pk)
        .filter(
            Q(status=Payment.Status.CREATED)
            | Q(status=Payment.Status.PENDING, updated_at__lt=stale_before)
        )
        .update(status=Payment.Status.PENDING, updated_at=now)
    )
    if not claimed:
        return "no_change", order

    state = provider.capture_payment(payment)
    if state.event is not None:
        return apply_payment_confirmation(state.event)
    return "no_change", order


def _settle_state(order, payment, provider, state):
    if state.event is not None:
        return apply_payment_confirmation(state.event)
    if state.status == "approved" and provider.requires_capture:
        return capture_guarded(order, payment, provider)
    return "no_change", order


def reconcile_payment(payment):
    provider = providers.get_provider(payment.provider)
    state = provider.fetch_payment_state(payment)
    return _settle_state(payment.order, payment, provider, state)


def reconcile_order(order):
    for payment in order.payments.filter(status__in=OPEN_PAYMENT_STATUSES).order_by("-attempt"):
        if not payment.provider_payment_id:
            continue
        outcome = reconcile_payment(payment)
        if outcome[0] != "no_change":
            return outcome
    return "no_change", order


def reconcile_return(order, params):
    for payment in order.payments.filter(status__in=OPEN_PAYMENT_STATUSES).order_by("-attempt"):
        provider = providers.get_provider(payment.provider)
        result = provider.handle_return(order, payment, params)
        outcome = _settle_state(order, payment, provider, result)
        if outcome[0] != "no_change":
            return outcome
    return "no_change", order


def _alert(order, subject, body):
    logger.error("%s: %s (%s)", subject, body, order.reference)
    notifications_service.queue_staff_alert(subject, f"Order {order.reference}\n\n{body}", order)


def _record(order, action, detail=None):
    OrderAuditLog.objects.create(order=order, action=action, detail=detail or {})


def _hold_or_fulfill(order, now):
    reason = fraud_service.review_reason(order)
    if reason is None:
        _queue_fulfillment(order)
        return
    held = order_transition(
        order,
        to=Order.Status.NEEDS_REVIEW,
        from_statuses=Order.Status.PAID,
        extra={"review_reason": reason},
    )
    if held:
        FulfillmentJob.objects.get_or_create(order=order)
        _record(order, "fraud_review_hold", {"reason": reason})
        notifications_service.notify_order_needs_review(order)
        _alert(
            order,
            "Order held for fraud review",
            f"{reason} Release it from /store/manage/orders/{order.reference}/ once reviewed.",
        )
    else:
        _queue_fulfillment(order)


def _queue_fulfillment(order):
    job, _ = FulfillmentJob.objects.get_or_create(order=order)
    from . import fulfillment as fulfillment_service

    transaction.on_commit(lambda: fulfillment_service.fulfill_order(order.pk))
    return job


def apply_payment_confirmation(event):
    now = timezone.now()
    order = _resolve_order(event)
    if order is None:
        return "orphaned", None

    if event.event_type not in HANDLED_EVENT_TYPES:
        return "unhandled", order

    if not _event_is_bound(order, event):
        logger.warning(
            "Ignoring %s event %s: no matching payment attempt for order %s",
            event.provider,
            event.event_id,
            order.reference,
        )
        return "orphaned", None

    if event.event_type == "refund_updated":
        payment = _bound_payments(order, event).first()
        if payment is None:
            return "orphaned", None
        return refunds_service.apply_provider_refund_event(order, payment, event), order

    if event.event_type in {"dispute_updated", "dispute_closed"}:
        disputes_service.handle_dispute_event(order, _bound_payments(order, event).first(), event)
        return "processed", order

    if event.event_type in {"attempt_expired", "attempt_failed"}:
        return _apply_attempt_end(order, event, now)

    if event.event_type == "buyer_approved":
        payment = (
            _bound_payments(order, event)
            .filter(status__in=OPEN_PAYMENT_STATUSES)
            .order_by("-attempt")
            .first()
        )
        provider = providers.get_provider(event.provider)
        if payment is None or not provider.requires_capture:
            return "duplicate_transition", order
        return capture_guarded(order, payment, provider)

    if event.event_type == "payment_succeeded":
        if event.amount_minor != order.total_minor or event.currency != order.currency:
            order_transition(
                order,
                to=Order.Status.NEEDS_REVIEW,
                extra={"review_reason": "Webhook amount/currency did not match the order."},
            )
            _record(order, "amount_mismatch", {"event_amount": event.amount_minor})
            if _mark_payment_paid(order, event, now):
                _alert(
                    order,
                    "Payment amount does not match the order",
                    f"{event.provider} reported {event.amount_minor} {event.currency} "
                    f"(reference {event.provider_reference or event.provider_payment_id}) but "
                    f"the order total is {order.total_minor} {order.currency}. Review before "
                    "delivering or refunding.",
                )
            return "rejected_amount", order
        return _apply_success(order, event, now)

    if event.event_type == "payment_failed":
        failed_payments = list(
            _bound_payments(order, event).filter(status__in=OPEN_PAYMENT_STATUSES)
        )
        Payment.objects.filter(pk__in=[p.pk for p in failed_payments]).update(
            status=Payment.Status.FAILED, updated_at=now
        )
        for payment in failed_payments:
            fraud_service.record_payment_failure(payment, order.user)
        transitioned = order_transition(order, to=Order.Status.PAYMENT_FAILED)
        if transitioned:
            inventory_service.release_reservation_for_order(order, now=now)
            _record(order, "payment_failed")
            return "processed", order
        return "duplicate_transition", order

    if event.event_type == "dispute_created":
        disputes_service.handle_dispute_event(order, _bound_payments(order, event).first(), event)
        transitioned = order_transition(order, to=Order.Status.CHARGEBACK)
        if transitioned:
            _revoke_delivered_keys(order, now)
            _record(order, "chargeback")
            return "processed", order
        return "duplicate_transition", order

    return "unhandled", order


def _apply_success(order, event, now):
    transitioned = order_transition(
        order,
        to=Order.Status.PAID,
        from_statuses=Order.Status.PENDING_PAYMENT,
        extra={"paid_at": now, "payment_reference": event.provider_payment_id},
    )
    if transitioned:
        _mark_payment_paid(order, event, now)
        _hold_or_fulfill(order, now)
        _record(order, "paid")
        return "processed", order

    if order.status == Order.Status.EXPIRED:
        return _apply_late_payment(order, event, now)

    if _bound_payments(order, event).filter(status=Payment.Status.PAID).exists():
        return "duplicate_transition", order

    _mark_payment_paid(order, event, now)
    _record(
        order,
        "unexpected_payment",
        {
            "provider": event.provider,
            "reference": event.provider_reference or event.provider_payment_id,
            "amount_minor": event.amount_minor,
            "order_status": order.status,
        },
    )
    _alert(
        order,
        "Payment received for an order that cannot use it",
        f"{event.provider} confirmed a payment of {event.amount_minor} {event.currency} "
        f"(reference {event.provider_reference or event.provider_payment_id}) for order "
        f"{order.reference}, which is in status '{order.status}'. Money has been taken and "
        "nothing was delivered for this payment. Refund it or resolve it manually.",
    )
    return "needs_attention", order


def _apply_late_payment(order, event, now):
    within_window = order.updated_at >= now - timezone.timedelta(hours=LATE_PAYMENT_WINDOW_HOURS)
    if not within_window:
        order_transition(
            order,
            to=Order.Status.NEEDS_REVIEW,
            extra={"review_reason": "Late payment received outside the recovery window."},
        )
        _record(order, "late_payment_outside_window")
        return "processed", order

    promoted = order_transition(
        order,
        to=Order.Status.PAID_LATE,
        from_statuses=Order.Status.EXPIRED,
        extra={"paid_at": now, "payment_reference": event.provider_payment_id},
    )
    if not promoted:
        return "duplicate_transition", order

    try:
        for item in order.items.select_related("product").all():
            if not hasattr(item, "reserved_key") or item.reserved_key is None:
                inventory_service.reserve_key_for_item(item, now=now)
    except inventory_service.OutOfStock:
        order_transition(
            order,
            to=Order.Status.NEEDS_REVIEW,
            from_statuses=Order.Status.PAID_LATE,
            extra={"review_reason": "Stock unavailable for late-payment recovery."},
        )
        _record(order, "late_payment_out_of_stock")
        return "processed", order

    order_transition(order, to=Order.Status.PAID, from_statuses=Order.Status.PAID_LATE)
    _mark_payment_paid(order, event, now)
    _hold_or_fulfill(order, now)
    _record(order, "paid_late_recovered")
    return "processed", order


def _revoke_delivered_keys(order, now):
    from marketplace.models import GameKey

    item_ids = list(order.items.values_list("pk", flat=True))
    GameKey.objects.filter(reserved_by_item_id__in=item_ids).exclude(
        status__in=[GameKey.Status.AVAILABLE, GameKey.Status.RESERVED]
    ).update(status=GameKey.Status.REVOKED, revoked_at=now, updated_at=now)
