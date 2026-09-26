from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimestampedModel

from .orders import Order


class BuyerVerification(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="marketplace_verification"
    )
    email = models.EmailField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    def is_verified_for(self, email):
        return bool(self.verified_at) and self.email.lower() == (email or "").lower()

    def __str__(self):
        return f"BuyerVerification({self.user})"


class RateLimitCounter(TimestampedModel):
    key = models.CharField(max_length=150, unique=True)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.key}: {self.count}"


class FulfillmentJob(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"
        NEEDS_REVIEW = "needs_review"

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="fulfillment_job")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    run_after = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["run_after"]
        indexes = [
            models.Index(fields=["status", "run_after"], name="fulfillmentjob_status_run_idx"),
        ]

    def __str__(self):
        return f"FulfillmentJob({self.order.reference})"


class OutboundEmail(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        SENDING = "sending"
        SENT = "sent"
        FAILED = "failed"

    status = models.CharField(max_length=7, choices=Status.choices, default=Status.PENDING)
    to_email = models.EmailField()
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    run_after = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    related_order = models.ForeignKey(
        Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="outbound_emails"
    )

    class Meta:
        ordering = ["run_after"]
        indexes = [
            models.Index(fields=["status", "run_after"], name="outboundemail_status_run_idx"),
        ]

    def __str__(self):
        return f"OutboundEmail({self.to_email}: {self.subject})"


class CronLease(TimestampedModel):
    name = models.CharField(max_length=64, unique=True)
    holder = models.UUIDField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class OrderAuditLog(TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="audit_log")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.reference}: {self.action}"


class MarketplaceSettings(TimestampedModel):
    marketplace_enabled = models.BooleanField(default=False)
    purchases_enabled = models.BooleanField(default=True)
    fulfillment_enabled = models.BooleanField(default=True)
    stripe_enabled = models.BooleanField(default=False)
    paypal_enabled = models.BooleanField(default=False)
    maintenance_message = models.CharField(max_length=255, blank=True)

    class Meta:
        permissions = [
            ("toggle_killswitch", "Can toggle marketplace kill switches"),
            ("view_dashboard", "Can view the marketplace admin dashboard"),
        ]

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Marketplace settings"


class SupplierIntegration(TimestampedModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=110, unique=True)
    adapter_path = models.CharField(max_length=255)
    enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AuditLog(TimestampedModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"], name="auditlog_action_created_idx"),
        ]

    def __str__(self):
        return f"{self.action}: {self.summary}"
