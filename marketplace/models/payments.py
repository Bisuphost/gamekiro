from django.conf import settings
from django.db import models

from core.models import TimestampedModel

from .orders import Order


class Payment(TimestampedModel):
    class Status(models.TextChoices):
        CREATED = "created"
        PENDING = "pending"
        PAID = "paid"
        FAILED = "failed"
        CANCELLED = "cancelled"
        REFUNDED = "refunded"
        DISPUTED = "disputed"
        EXPIRED = "expired"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=32)
    provider_payment_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED)
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    idempotency_key = models.CharField(max_length=64, unique=True)
    provider_status = models.CharField(max_length=64, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    provider_reference = models.CharField(max_length=255, blank=True)
    checkout_url = models.URLField(max_length=1000, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)
    refunded_minor = models.PositiveBigIntegerField(default=0)
    created_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_payment_id"],
                name="uniq_payment_provider_payment_id",
            ),
            models.UniqueConstraint(fields=["order", "attempt"], name="uniq_payment_order_attempt"),
            models.CheckConstraint(
                condition=models.Q(refunded_minor__lte=models.F("amount_minor")),
                name="payment_refunded_not_above_amount",
            ),
        ]

    def __str__(self):
        return f"Payment #{self.pk} for {self.order.reference}"


class WebhookEvent(TimestampedModel):
    class Status(models.TextChoices):
        RECEIVED = "received"
        PROCESSED = "processed"
        DUPLICATE_TRANSITION = "duplicate_transition"
        ORPHANED = "orphaned"
        REJECTED_AMOUNT = "rejected_amount"
        FAILED = "failed"

    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=64)
    order = models.ForeignKey(
        Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="webhook_events"
    )
    amount_minor = models.PositiveBigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=21, choices=Status.choices, default=Status.RECEIVED)
    attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "event_id"], name="uniq_webhookevent_provider_event_id"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "locked_until"], name="webhookevent_status_locked_idx"),
        ]

    def __str__(self):
        return f"{self.provider}:{self.event_id}"


class Refund(TimestampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested"
        APPROVED = "approved"
        PROCESSING = "processing"
        COMPLETED = "completed"
        REJECTED = "rejected"
        FAILED = "failed"

    class Source(models.TextChoices):
        ADMIN = "admin"
        PROVIDER = "provider"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="refunds")
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="refunds")
    amount_minor = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=11, choices=Status.choices, default=Status.REQUESTED)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.ADMIN)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    provider_refund_id = models.CharField(max_length=255, null=True, blank=True)
    provider_status = models.CharField(max_length=64, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["payment", "provider_refund_id"], name="uniq_refund_payment_provider_id"
            ),
        ]

    def __str__(self):
        return f"Refund #{self.pk} for {self.order.reference}"


class Dispute(TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="disputes")
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="disputes"
    )
    provider = models.CharField(max_length=32)
    provider_dispute_id = models.CharField(max_length=255)
    status = models.CharField(max_length=64, blank=True)
    reason = models.CharField(max_length=128, blank=True)
    amount_minor = models.PositiveBigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    response_due_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_dispute_id"], name="uniq_dispute_provider_id"
            ),
        ]

    def __str__(self):
        return f"Dispute {self.provider}:{self.provider_dispute_id}"
