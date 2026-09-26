import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from marketplace.models import (
    Entitlement,
    FulfillmentJob,
    Order,
    OrderAuditLog,
    OrderItem,
    order_transition,
)

from . import flags as flags_service
from . import inventory as inventory_service

logger = logging.getLogger("marketplace")

NON_FULFILLABLE_STATUSES = {
    Order.Status.PENDING_PAYMENT,
    Order.Status.PAYMENT_FAILED,
    Order.Status.EXPIRED,
    Order.Status.CANCELLED,
    Order.Status.REFUNDED,
    Order.Status.PARTIALLY_REFUNDED,
    Order.Status.CHARGEBACK,
}

JOB_LEASE_MINUTES = 5
MAX_ATTEMPTS = 8


def _record(order, action, detail=None):
    OrderAuditLog.objects.create(order=order, action=action, detail=detail or {})


def _claim_job(order_id, now):
    claimed = FulfillmentJob.objects.filter(order_id=order_id).exclude(
        status=FulfillmentJob.Status.DONE
    )
    updated = claimed.filter(locked_until__isnull=True).update(
        status=FulfillmentJob.Status.RUNNING,
        locked_until=now + timedelta(minutes=JOB_LEASE_MINUTES),
        updated_at=now,
    ) or claimed.filter(locked_until__lt=now).update(
        status=FulfillmentJob.Status.RUNNING,
        locked_until=now + timedelta(minutes=JOB_LEASE_MINUTES),
        updated_at=now,
    )
    if not updated:
        return None
    try:
        return FulfillmentJob.objects.get(order_id=order_id)
    except FulfillmentJob.DoesNotExist:
        return None


def fulfill_order(order_id):
    if not flags_service.fulfillment_enabled():
        return "disabled"

    now = timezone.now()
    job = _claim_job(order_id, now)
    if job is None:
        return "skipped"

    try:
        order = Order.objects.get(pk=order_id)
    except Order.DoesNotExist:
        FulfillmentJob.objects.filter(pk=job.pk).update(
            status=FulfillmentJob.Status.FAILED, last_error="Order not found", updated_at=now
        )
        return "failed"

    if order.status in NON_FULFILLABLE_STATUSES:
        FulfillmentJob.objects.filter(pk=job.pk).update(
            status=FulfillmentJob.Status.FAILED,
            locked_until=None,
            last_error=f"Order is {order.status}; nothing to deliver.",
            updated_at=now,
        )
        return "skipped"

    items = list(order.items.select_related("product").all())
    any_failed = False

    for item in items:
        if item.delivered_at is not None:
            continue
        try:
            with transaction.atomic():
                success = inventory_service.assign_key_for_item(item, now=timezone.now())
        except Exception:
            logger.exception("Fulfilment failed for order item %s", item.pk)
            success = False
        if success:
            _try_create_entitlement(item, order)
        else:
            any_failed = True

    item_ids = [item.pk for item in items]
    remaining_undelivered = OrderItem.objects.filter(
        pk__in=item_ids, delivered_at__isnull=True
    ).count()
    all_delivered = remaining_undelivered == 0

    final_now = timezone.now()

    if all_delivered:
        order_transition(order, to=Order.Status.FULFILLED, extra={"fulfilled_at": final_now})
        FulfillmentJob.objects.filter(pk=job.pk).update(
            status=FulfillmentJob.Status.DONE, locked_until=None, updated_at=final_now
        )
        _record(order, "fulfilled")
        _queue_confirmation_email(order)
        return "done"

    attempts = job.attempts + 1
    if attempts >= MAX_ATTEMPTS:
        order_transition(
            order,
            to=Order.Status.NEEDS_REVIEW,
            extra={"review_reason": "Fulfilment failed after repeated retries."},
        )
        FulfillmentJob.objects.filter(pk=job.pk).update(
            status=FulfillmentJob.Status.NEEDS_REVIEW,
            attempts=attempts,
            locked_until=None,
            last_error="Out of stock or repeated failure allocating keys.",
            updated_at=final_now,
        )
        _record(order, "fulfillment_needs_review")
        return "needs_review"

    partial_status = (
        Order.Status.PARTIALLY_FULFILLED if any_failed else Order.Status.FULFILLMENT_FAILED
    )
    order_transition(order, to=partial_status)
    FulfillmentJob.objects.filter(pk=job.pk).update(
        status=FulfillmentJob.Status.PENDING,
        attempts=attempts,
        locked_until=None,
        run_after=final_now + timedelta(minutes=2 * attempts),
        updated_at=final_now,
    )
    _record(order, "fulfillment_partial_retry")
    return "partial"


def _try_create_entitlement(order_item, order):
    Entitlement.objects.get_or_create(
        order_item=order_item,
        defaults={"user": order.user, "product": order_item.product},
    )


def _queue_confirmation_email(order):
    from . import notifications as notifications_service

    notifications_service.queue_order_confirmation_email(order)
