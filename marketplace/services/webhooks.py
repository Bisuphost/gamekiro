import logging
import traceback
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from marketplace import providers
from marketplace.models import WebhookEvent
from marketplace.providers.base import WebhookVerificationError, WebhookVerificationUnavailable

from . import payments as payments_service

logger = logging.getLogger("marketplace")

KNOWN_EVENT_TYPES = payments_service.HANDLED_EVENT_TYPES
LEASE_MINUTES = 5

STATUS_BY_OUTCOME = {
    "processed": WebhookEvent.Status.PROCESSED,
    "duplicate_transition": WebhookEvent.Status.DUPLICATE_TRANSITION,
    "rejected_amount": WebhookEvent.Status.REJECTED_AMOUNT,
    "unhandled": WebhookEvent.Status.PROCESSED,
    "needs_attention": WebhookEvent.Status.PROCESSED,
}


def handle(provider_slug, raw_body, headers):
    provider = providers.get_provider(provider_slug)
    try:
        event = provider.verify_webhook(raw_body, headers)
    except WebhookVerificationUnavailable as exc:
        logger.warning("Webhook verification unavailable for %s: %s", provider_slug, exc)
        return 503, "verification unavailable"
    except WebhookVerificationError as exc:
        return 400, str(exc)

    now = timezone.now()

    if event.event_type not in KNOWN_EVENT_TYPES:
        try:
            with transaction.atomic():
                WebhookEvent.objects.create(
                    provider=provider_slug,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    payload=event.raw,
                    amount_minor=event.amount_minor,
                    currency=event.currency,
                    status=WebhookEvent.Status.PROCESSED,
                    processed_at=now,
                )
        except IntegrityError:
            pass
        return 200, "ignored"

    try:
        with transaction.atomic():
            webhook_event = WebhookEvent.objects.create(
                provider=provider_slug,
                event_id=event.event_id,
                event_type=event.event_type,
                payload=event.raw,
                amount_minor=event.amount_minor,
                currency=event.currency,
            )
    except IntegrityError:
        webhook_event = WebhookEvent.objects.get(provider=provider_slug, event_id=event.event_id)

    claimed = (
        WebhookEvent.objects.filter(pk=webhook_event.pk, processed_at__isnull=True)
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=now))
        .update(
            locked_until=now + timedelta(minutes=LEASE_MINUTES),
            attempts=F("attempts") + 1,
            updated_at=now,
        )
    )
    if claimed != 1:
        return 200, "already processing or processed"

    try:
        outcome, order = payments_service.apply_payment_confirmation(event)
    except Exception:
        logger.exception("Webhook processing raised for %s:%s", provider_slug, event.event_id)
        WebhookEvent.objects.filter(pk=webhook_event.pk).update(
            locked_until=None,
            status=WebhookEvent.Status.FAILED,
            last_error=traceback.format_exc()[:2000],
            updated_at=timezone.now(),
        )
        return 500, "internal error"

    finish_time = timezone.now()
    if outcome == "orphaned":
        WebhookEvent.objects.filter(pk=webhook_event.pk).update(
            status=WebhookEvent.Status.ORPHANED,
            locked_until=None,
            updated_at=finish_time,
        )
        return 200, "orphaned"

    WebhookEvent.objects.filter(pk=webhook_event.pk).update(
        status=STATUS_BY_OUTCOME.get(outcome, WebhookEvent.Status.PROCESSED),
        order=order,
        processed_at=finish_time,
        locked_until=None,
        updated_at=finish_time,
    )
    return 200, outcome
