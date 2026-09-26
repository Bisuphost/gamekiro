import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from marketplace import providers
from marketplace.models import GameKey, Order, OrderAuditLog, Payment, Refund, order_transition
from marketplace.providers.base import ProviderError

from . import notifications as notifications_service

logger = logging.getLogger("marketplace")

REFUNDABLE_ORDER_STATUSES = {
    Order.Status.PAID,
    Order.Status.PARTIALLY_FULFILLED,
    Order.Status.FULFILLED,
    Order.Status.FULFILLMENT_FAILED,
    Order.Status.NEEDS_REVIEW,
    Order.Status.PARTIALLY_REFUNDED,
}
RESERVING_STATUSES = [Refund.Status.REQUESTED, Refund.Status.PROCESSING, Refund.Status.COMPLETED]
OPEN_REFUND_STATUSES = [Refund.Status.REQUESTED, Refund.Status.PROCESSING]
SUCCESS_STATUSES = {"succeeded", "completed"}
FAILURE_STATUSES = {"failed", "canceled", "cancelled"}
RESUBMIT_AFTER_MINUTES = 5


class RefundError(Exception):
    pass


def paying_payment(order):
    return order.payments.filter(status=Payment.Status.PAID).order_by("confirmed_at", "id").first()


def refundable_minor(payment):
    return payment.amount_minor - payment.refunded_minor


def _record(order, action, detail=None):
    OrderAuditLog.objects.create(order=order, action=action, detail=detail or {})


def _alert(order, subject, body):
    logger.error("%s: %s (%s)", subject, body, order.reference)
    notifications_service.queue_staff_alert(subject, f"Order {order.reference}\n\n{body}", order)


def _reserve(payment, amount_minor):
    return Payment.objects.filter(
        pk=payment.pk, refunded_minor__lte=F("amount_minor") - amount_minor
    ).update(refunded_minor=F("refunded_minor") + amount_minor, updated_at=timezone.now())


def _release(payment, amount_minor):
    Payment.objects.filter(pk=payment.pk, refunded_minor__gte=amount_minor).update(
        refunded_minor=F("refunded_minor") - amount_minor, updated_at=timezone.now()
    )


def request_refund(order, payment, amount_minor, reason, user):
    if order.status not in REFUNDABLE_ORDER_STATUSES and payment != paying_payment(order):
        raise RefundError("This order cannot be refunded in its current state.")
    if payment.order_id != order.pk or payment.status != Payment.Status.PAID:
        raise RefundError("That payment is not a settled payment of this order.")
    if amount_minor <= 0:
        raise RefundError("The refund amount must be greater than zero.")
    with transaction.atomic():
        if not _reserve(payment, amount_minor):
            raise RefundError("The refund exceeds the amount still refundable on this payment.")
        refund = Refund.objects.create(
            order=order,
            payment=payment,
            amount_minor=amount_minor,
            reason=reason[:255],
            requested_by=user,
        )
        refund.idempotency_key = f"refund-{refund.pk}"
        refund.save(update_fields=["idempotency_key", "updated_at"])
    return refund


def submit_refund(refund):
    now = timezone.now()
    claimed = Refund.objects.filter(pk=refund.pk, status=Refund.Status.REQUESTED).update(
        status=Refund.Status.PROCESSING, updated_at=now
    )
    if not claimed:
        return _reload(refund)
    return _call_provider(refund)


def resubmit_refund(refund):
    refund = _reload(refund)
    if refund.status != Refund.Status.PROCESSING:
        return refund
    return _call_provider(refund)


def _call_provider(refund):
    refund = _reload(refund)
    provider = providers.get_provider(refund.payment.provider)
    try:
        if refund.provider_refund_id:
            result = provider.fetch_refund(refund.payment, refund.provider_refund_id)
        else:
            result = provider.refund(
                refund.payment,
                refund.amount_minor,
                refund.idempotency_key,
                refund_id=refund.pk,
            )
    except ProviderError as exc:
        if exc.definitive:
            _fail(refund, f"Provider rejected the refund ({exc.code}).")
        else:
            Refund.objects.filter(pk=refund.pk).update(
                failure_reason=f"Awaiting confirmation ({exc.code or 'network'})",
                updated_at=timezone.now(),
            )
        return _reload(refund)
    return apply_result(refund, result.provider_refund_id, result.status, result.failure_reason)


def apply_result(refund, provider_refund_id, provider_status, failure_reason=""):
    now = timezone.now()
    normalized = (provider_status or "").lower()
    fields = {"provider_status": (provider_status or "")[:64], "updated_at": now}
    if provider_refund_id:
        fields["provider_refund_id"] = provider_refund_id
    Refund.objects.filter(pk=refund.pk).update(**fields)
    refund = _reload(refund)

    if normalized in SUCCESS_STATUSES:
        _complete(refund)
    elif normalized in FAILURE_STATUSES:
        _fail(refund, failure_reason or f"Provider reported the refund as {normalized}.")
    elif normalized == "requires_action":
        _alert(
            refund.order,
            "Refund needs action",
            f"Refund #{refund.pk} of {refund.amount_minor} {refund.order.currency} requires "
            "action in the payment provider dashboard.",
        )
    return _reload(refund)


def _complete(refund):
    now = timezone.now()
    updated = Refund.objects.filter(pk=refund.pk, status__in=OPEN_REFUND_STATUSES).update(
        status=Refund.Status.COMPLETED, processed_at=now, failure_reason="", updated_at=now
    )
    if not updated:
        return False
    order = refund.order
    _record(
        order,
        "refund_completed",
        {"refund": refund.pk, "amount_minor": refund.amount_minor, "source": refund.source},
    )
    if refund.payment_id != getattr(paying_payment(order), "pk", None):
        return True

    completed = (
        Refund.objects.filter(payment=refund.payment, status=Refund.Status.COMPLETED).aggregate(
            total=Sum("amount_minor")
        )["total"]
        or 0
    )
    new_status = (
        Order.Status.REFUNDED if completed >= order.total_minor else Order.Status.PARTIALLY_REFUNDED
    )
    order.refresh_from_db()
    order_transition(order, to=new_status)
    if new_status == Order.Status.REFUNDED:
        item_ids = list(order.items.values_list("pk", flat=True))
        GameKey.objects.filter(reserved_by_item_id__in=item_ids).exclude(
            status=GameKey.Status.AVAILABLE
        ).update(status=GameKey.Status.REFUNDED, revoked_at=now, updated_at=now)
    return True


def _fail(refund, reason):
    now = timezone.now()
    updated = Refund.objects.filter(
        pk=refund.pk, status__in=OPEN_REFUND_STATUSES + [Refund.Status.COMPLETED]
    ).update(status=Refund.Status.FAILED, failure_reason=reason[:255], updated_at=now)
    if not updated:
        return False
    _release(refund.payment, refund.amount_minor)
    _record(refund.order, "refund_failed", {"refund": refund.pk, "reason": reason[:255]})
    _alert(
        refund.order,
        "Refund failed",
        f"Refund #{refund.pk} of {refund.amount_minor} {refund.order.currency} failed: {reason} "
        "The customer has not been repaid; resolve it in the provider dashboard.",
    )
    return True


def _reload(refund):
    return Refund.objects.select_related("order", "payment").get(pk=refund.pk)


def apply_provider_refund_event(order, payment, event):
    refund = None
    if event.provider_refund_id:
        refund = Refund.objects.filter(
            payment=payment, provider_refund_id=event.provider_refund_id
        ).first()
    refund_pk = (event.detail or {}).get("refund_id")
    if refund is None and refund_pk and str(refund_pk).isdigit():
        refund = Refund.objects.filter(pk=int(refund_pk), payment=payment).first()

    if refund is not None:
        result = apply_result(
            refund,
            event.provider_refund_id,
            event.refund_status,
            (event.detail or {}).get("failure_reason", ""),
        )
        return "processed" if result.status != refund.status else "duplicate_transition"

    if not event.provider_refund_id or event.amount_minor is None:
        return "duplicate_transition"
    if not _reserve(payment, event.amount_minor):
        _alert(
            order,
            "Provider refund exceeds the recorded payment",
            f"{event.provider} reports refund {event.provider_refund_id} of {event.amount_minor} "
            f"{event.currency}, which is more than remains refundable on the payment.",
        )
        return "needs_attention"
    try:
        with transaction.atomic():
            refund = Refund.objects.create(
                order=order,
                payment=payment,
                amount_minor=event.amount_minor,
                source=Refund.Source.PROVIDER,
                status=Refund.Status.PROCESSING,
                provider_refund_id=event.provider_refund_id,
                reason="Issued outside GameKiro",
            )
    except IntegrityError:
        _release(payment, event.amount_minor)
        return "duplicate_transition"
    apply_result(refund, event.provider_refund_id, event.refund_status)
    _alert(
        order,
        "Refund issued outside GameKiro",
        f"{event.provider} refund {event.provider_refund_id} of {event.amount_minor} "
        f"{event.currency} was made in the provider dashboard.",
    )
    return "processed"


def reconcile_processing_refunds(budget=20):
    cutoff = timezone.now() - timedelta(minutes=RESUBMIT_AFTER_MINUTES)
    candidates = list(
        Refund.objects.filter(status=Refund.Status.PROCESSING, updated_at__lt=cutoff)
        .exclude(payment__provider="mock")
        .select_related("order", "payment")
        .order_by("updated_at")[:budget]
    )
    settled = errors = 0
    for refund in candidates:
        try:
            result = _call_provider(refund)
        except Exception:
            logger.exception("Refund reconciliation failed for refund %s", refund.pk)
            errors += 1
            continue
        Refund.objects.filter(pk=refund.pk, status=Refund.Status.PROCESSING).update(
            updated_at=timezone.now()
        )
        if result.status != Refund.Status.PROCESSING:
            settled += 1
    return {"checked": len(candidates), "settled": settled, "errors": errors}
