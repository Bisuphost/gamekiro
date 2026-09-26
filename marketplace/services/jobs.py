import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F, Q
from django.utils import timezone

from marketplace import providers
from marketplace.models import (
    CronLease,
    FulfillmentJob,
    Order,
    OutboundEmail,
    Payment,
    WebhookEvent,
)
from marketplace.providers.base import ProviderError, WebhookVerificationError

from . import fulfillment as fulfillment_service
from . import inventory as inventory_service
from . import notifications as notifications_service
from . import payments as payments_service
from . import ratelimit as ratelimit_service
from . import refunds as refunds_service
from .webhooks import STATUS_BY_OUTCOME

logger = logging.getLogger("marketplace")

LEASE_TTL_SECONDS = 55
CLAIM_LEASE_MINUTES = 5
ORPHAN_GIVE_UP_HOURS = 48
PULL_MIN_AGE_MINUTES = 3
PULL_RECHECK_MINUTES = 3
PAYLOAD_RETENTION_DAYS = 90


def acquire_lease(name, ttl_seconds=LEASE_TTL_SECONDS):
    now = timezone.now()
    holder = uuid.uuid4()
    lease, created = CronLease.objects.get_or_create(
        name=name, defaults={"holder": holder, "expires_at": now + timedelta(seconds=ttl_seconds)}
    )
    if created:
        return holder

    claimed = CronLease.objects.filter(pk=lease.pk, expires_at__lt=now).update(
        holder=holder, expires_at=now + timedelta(seconds=ttl_seconds), updated_at=now
    )
    return holder if claimed else None


def release_lease(name, holder):
    CronLease.objects.filter(name=name, holder=holder).update(
        expires_at=timezone.now(), updated_at=timezone.now()
    )


def expire_reservations():
    expired_orders, released_keys = inventory_service.sweep_expired_reservations()
    return {"expired_orders": expired_orders, "released_keys": released_keys}


def drain_fulfillment_jobs(budget=20):
    now = timezone.now()
    order_ids = list(
        FulfillmentJob.objects.filter(status=FulfillmentJob.Status.PENDING, run_after__lte=now)
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
        .values_list("order_id", flat=True)[:budget]
    )
    results = [fulfillment_service.fulfill_order(order_id) for order_id in order_ids]
    return {"attempted": len(results), "results": results}


def reconcile_stuck_webhooks(budget=20):
    now = timezone.now()
    stuck = list(
        WebhookEvent.objects.filter(processed_at__isnull=True)
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
        .order_by("created_at")[:budget]
    )
    redriven = 0
    for event_row in stuck:
        claimed = (
            WebhookEvent.objects.filter(pk=event_row.pk, processed_at__isnull=True)
            .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
            .update(
                locked_until=now + timedelta(minutes=CLAIM_LEASE_MINUTES),
                attempts=F("attempts") + 1,
                updated_at=now,
            )
        )
        if not claimed:
            continue

        try:
            event = providers.get_provider(event_row.provider).normalize_event(event_row.payload)
        except (ValueError, WebhookVerificationError):
            logger.exception("Cannot rebuild webhook event %s for re-drive", event_row.pk)
            WebhookEvent.objects.filter(pk=event_row.pk).update(
                locked_until=None, status=WebhookEvent.Status.FAILED, updated_at=timezone.now()
            )
            continue
        try:
            outcome, order = payments_service.apply_payment_confirmation(event)
        except Exception:
            logger.exception("Reconciliation raised for webhook event %s", event_row.pk)
            WebhookEvent.objects.filter(pk=event_row.pk).update(
                locked_until=None, status=WebhookEvent.Status.FAILED, updated_at=timezone.now()
            )
            continue

        finish = timezone.now()
        if outcome == "orphaned":
            give_up = event_row.created_at < finish - timedelta(hours=ORPHAN_GIVE_UP_HOURS)
            WebhookEvent.objects.filter(pk=event_row.pk).update(
                status=WebhookEvent.Status.ORPHANED,
                locked_until=None,
                processed_at=finish if give_up else None,
                last_error="No matching order or payment attempt; gave up." if give_up else "",
                updated_at=finish,
            )
            if give_up:
                notifications_service.queue_staff_alert(
                    "Webhook event could not be matched to an order",
                    f"{event_row.provider} event {event_row.event_id} ({event_row.event_type}) "
                    f"was received {ORPHAN_GIVE_UP_HOURS} hours ago and still matches no order "
                    "or payment attempt. If it represents money taken, resolve it manually.",
                )
        else:
            WebhookEvent.objects.filter(pk=event_row.pk).update(
                status=STATUS_BY_OUTCOME.get(outcome, WebhookEvent.Status.PROCESSED),
                order=order,
                processed_at=finish,
                locked_until=None,
                updated_at=finish,
            )
        redriven += 1
    return {"redriven": redriven}


def pull_reconciliation(budget=20):
    now = timezone.now()
    candidates = list(
        Payment.objects.filter(
            status__in=payments_service.OPEN_PAYMENT_STATUSES,
            order__status=Order.Status.PENDING_PAYMENT,
            created_at__lt=now - timedelta(minutes=PULL_MIN_AGE_MINUTES),
        )
        .exclude(provider="mock")
        .filter(
            Q(reconciled_at__isnull=True)
            | Q(reconciled_at__lt=now - timedelta(minutes=PULL_RECHECK_MINUTES))
        )
        .select_related("order")
        .order_by("created_at")[:budget]
    )
    checked = changed = recovered = errors = 0
    for payment in candidates:
        Payment.objects.filter(pk=payment.pk).update(reconciled_at=now)
        checked += 1
        try:
            if not payment.provider_payment_id:
                if payments_service.begin_payment(payment.order, payment) is not None:
                    recovered += 1
                continue
            outcome, _ = payments_service.reconcile_payment(payment)
        except (ProviderError, WebhookVerificationError, ValueError):
            logger.exception("Pull reconciliation failed for payment %s", payment.pk)
            errors += 1
            continue
        if outcome != "no_change":
            changed += 1
    return {"checked": checked, "changed": changed, "recovered": recovered, "errors": errors}


def reconcile_refunds(budget=20):
    return refunds_service.reconcile_processing_refunds(budget=budget)


def purge_old_webhook_payloads(days=PAYLOAD_RETENTION_DAYS):
    cutoff = timezone.now() - timedelta(days=days)
    return (
        WebhookEvent.objects.filter(processed_at__isnull=False, created_at__lt=cutoff)
        .exclude(payload={})
        .update(payload={})
    )


def drain_outbound_email(budget=20):
    now = timezone.now()
    candidates = list(
        OutboundEmail.objects.filter(
            status__in=[OutboundEmail.Status.PENDING, OutboundEmail.Status.FAILED],
            run_after__lte=now,
        )
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
        .order_by("run_after")[:budget]
    )
    sent = failed = 0
    for email in candidates:
        claimed = (
            OutboundEmail.objects.filter(pk=email.pk)
            .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
            .update(
                locked_until=now + timedelta(minutes=CLAIM_LEASE_MINUTES),
                status=OutboundEmail.Status.SENDING,
                attempts=F("attempts") + 1,
                updated_at=now,
            )
        )
        if not claimed:
            continue
        try:
            send_mail(
                subject=email.subject,
                message=email.body_text,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email.to_email],
                fail_silently=False,
            )
        except Exception as exc:
            logger.exception("Outbound email %s failed", email.pk)
            OutboundEmail.objects.filter(pk=email.pk).update(
                status=OutboundEmail.Status.FAILED,
                last_error=str(exc)[:500],
                locked_until=None,
                updated_at=timezone.now(),
            )
            failed += 1
        else:
            OutboundEmail.objects.filter(pk=email.pk).update(
                status=OutboundEmail.Status.SENT, locked_until=None, updated_at=timezone.now()
            )
            sent += 1
    return {"sent": sent, "failed": failed}


def heartbeat():
    CronLease.objects.update_or_create(
        name="heartbeat", defaults={"holder": None, "expires_at": timezone.now()}
    )


def purge_stale_rate_limits():
    return ratelimit_service.purge_stale()
