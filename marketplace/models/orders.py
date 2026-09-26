import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimestampedModel

from .catalog import Product


def generate_order_reference():
    return f"ORD-{secrets.token_hex(8).upper()}"


class Order(TimestampedModel):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment"
        PAID = "paid"
        PAID_LATE = "paid_late"
        PARTIALLY_FULFILLED = "partially_fulfilled"
        FULFILLED = "fulfilled"
        PAYMENT_FAILED = "payment_failed"
        FULFILLMENT_FAILED = "fulfillment_failed"
        EXPIRED = "expired"
        CANCELLED = "cancelled"
        REFUNDED = "refunded"
        PARTIALLY_REFUNDED = "partially_refunded"
        CHARGEBACK = "chargeback"
        NEEDS_REVIEW = "needs_review"

    reference = models.CharField(
        max_length=32, unique=True, editable=False, default=generate_order_reference
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="marketplace_orders"
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_PAYMENT)
    subtotal_minor = models.PositiveBigIntegerField(default=0)
    discount_minor = models.PositiveBigIntegerField(default=0)
    tax_minor = models.PositiveBigIntegerField(default=0)
    total_minor = models.PositiveBigIntegerField(default=0)
    currency = models.CharField(max_length=3)
    coupon = models.ForeignKey(
        "marketplace.Coupon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    provider = models.CharField(max_length=32, blank=True)
    provider_session_id = models.CharField(max_length=255, null=True, blank=True)
    payment_reference = models.CharField(max_length=255, blank=True)
    reservation_expires_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_session_id"],
                name="uniq_order_provider_session",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="order_user_status_idx"),
            models.Index(
                fields=["status", "reservation_expires_at"], name="order_status_expiry_idx"
            ),
        ]
        permissions = [
            ("fulfill_order", "Can manually re-drive order fulfilment"),
            ("refund_order", "Can initiate or approve order refunds"),
        ]

    def __str__(self):
        return self.reference


ALLOWED_TRANSITIONS = {
    Order.Status.PENDING_PAYMENT: {
        Order.Status.PAID,
        Order.Status.PAYMENT_FAILED,
        Order.Status.EXPIRED,
        Order.Status.CANCELLED,
        Order.Status.NEEDS_REVIEW,
    },
    Order.Status.PAID: {
        Order.Status.FULFILLED,
        Order.Status.PARTIALLY_FULFILLED,
        Order.Status.FULFILLMENT_FAILED,
        Order.Status.NEEDS_REVIEW,
        Order.Status.REFUNDED,
        Order.Status.PARTIALLY_REFUNDED,
        Order.Status.CHARGEBACK,
    },
    Order.Status.PAID_LATE: {
        Order.Status.PAID,
        Order.Status.NEEDS_REVIEW,
    },
    Order.Status.PARTIALLY_FULFILLED: {
        Order.Status.FULFILLED,
        Order.Status.FULFILLMENT_FAILED,
        Order.Status.NEEDS_REVIEW,
        Order.Status.CHARGEBACK,
        Order.Status.REFUNDED,
        Order.Status.PARTIALLY_REFUNDED,
    },
    Order.Status.FULFILLMENT_FAILED: {
        Order.Status.NEEDS_REVIEW,
        Order.Status.FULFILLED,
        Order.Status.PARTIALLY_FULFILLED,
        Order.Status.REFUNDED,
        Order.Status.PARTIALLY_REFUNDED,
    },
    Order.Status.EXPIRED: {
        Order.Status.PAID_LATE,
        Order.Status.NEEDS_REVIEW,
    },
    Order.Status.FULFILLED: {
        Order.Status.REFUNDED,
        Order.Status.PARTIALLY_REFUNDED,
        Order.Status.CHARGEBACK,
    },
    Order.Status.NEEDS_REVIEW: {
        Order.Status.FULFILLED,
        Order.Status.PARTIALLY_FULFILLED,
        Order.Status.REFUNDED,
        Order.Status.PARTIALLY_REFUNDED,
        Order.Status.CANCELLED,
        Order.Status.CHARGEBACK,
    },
    Order.Status.PARTIALLY_REFUNDED: {
        Order.Status.REFUNDED,
        Order.Status.CHARGEBACK,
    },
}


def order_transition(order, *, to, extra=None, from_statuses=None):
    valid_sources = {status for status, targets in ALLOWED_TRANSITIONS.items() if to in targets}
    if from_statuses is not None:
        requested = (
            {from_statuses}
            if isinstance(from_statuses, (str, Order.Status))
            else set(from_statuses)
        )
        valid_sources &= requested
    now = timezone.now()
    values = {"status": to, "updated_at": now}
    if extra:
        values.update(extra)
    updated = Order.objects.filter(pk=order.pk, status__in=valid_sources).update(**values)
    if updated:
        order.status = to
        order.updated_at = now
        if extra:
            for key, value in extra.items():
                setattr(order, key, value)
        return True
    return False


class OrderItem(TimestampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    unit_price_minor = models.PositiveBigIntegerField()
    line_group = models.UUIDField(default=uuid.uuid4, editable=False)
    delivered_at = models.DateTimeField(null=True, blank=True)
    product_title = models.CharField(max_length=255)
    platform_name = models.CharField(max_length=100, blank=True)
    region_name = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order"], name="orderitem_order_idx"),
            models.Index(fields=["line_group"], name="orderitem_linegroup_idx"),
        ]

    def __str__(self):
        return f"{self.product_title} ({self.order.reference})"


class Entitlement(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="marketplace_library"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="entitlements")
    order_item = models.OneToOneField(
        OrderItem, on_delete=models.PROTECT, related_name="entitlement"
    )
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-granted_at"]
        indexes = [
            models.Index(fields=["user", "product"], name="entitlement_user_product_idx"),
        ]

    def __str__(self):
        return f"{self.user}: {self.product}"
