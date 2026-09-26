import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from marketplace.models import Dispute, OrderAuditLog

from . import notifications as notifications_service

logger = logging.getLogger("marketplace")


def _due_at(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=dt_timezone.utc)
    parsed = parse_datetime(str(value))
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _alert(order, subject, body):
    logger.error("%s: %s (%s)", subject, body, order.reference)
    notifications_service.queue_staff_alert(subject, f"Order {order.reference}\n\n{body}", order)


def record_dispute(order, payment, event):
    detail = event.detail or {}
    dispute_id = detail.get("dispute_id")
    if not dispute_id:
        return None, False
    now = timezone.now()
    defaults = {
        "order": order,
        "payment": payment,
        "status": str(detail.get("status", ""))[:64],
        "reason": str(detail.get("reason", ""))[:128],
        "currency": event.currency,
    }
    if event.amount_minor is not None:
        defaults["amount_minor"] = event.amount_minor
    due = _due_at(detail.get("due_by"))
    if due is not None:
        defaults["response_due_at"] = due
    if event.event_type == "dispute_closed":
        defaults["closed_at"] = now
        defaults["outcome"] = str(detail.get("outcome", ""))[:64]
    dispute, created = Dispute.objects.update_or_create(
        provider=event.provider, provider_dispute_id=dispute_id, defaults=defaults
    )
    OrderAuditLog.objects.create(
        order=order,
        action=event.event_type,
        detail={"dispute": dispute_id, "status": dispute.status, "outcome": dispute.outcome},
    )
    return dispute, created


def handle_dispute_event(order, payment, event):
    dispute, created = record_dispute(order, payment, event)
    if created or (dispute is None and event.event_type == "dispute_created"):
        due = "unknown"
        if dispute is not None and dispute.response_due_at:
            due = dispute.response_due_at.isoformat()
        reference = dispute.provider_dispute_id if dispute else "(payment reversal)"
        _alert(
            order,
            "Dispute opened",
            f"{event.provider} dispute {reference} was opened for this order. "
            f"Respond in the provider dashboard before {due}.",
        )
    elif event.event_type == "dispute_closed" and dispute is not None:
        _alert(
            order,
            "Dispute closed",
            f"{event.provider} dispute {dispute.provider_dispute_id} closed with outcome "
            f"'{dispute.outcome or dispute.status}'. Keys revoked at the time of the dispute "
            "are not restored automatically.",
        )
    return dispute


def open_disputes():
    return Dispute.objects.filter(closed_at__isnull=True).select_related("order")
