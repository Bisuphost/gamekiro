from django.conf import settings
from django.db import models

from core.models import TimestampedModel

from .catalog import Product
from .orders import Order, OrderItem


class InventoryImportBatch(TimestampedModel):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="import_batches")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    source = models.CharField(max_length=32, default="manual")
    total_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("import_inventory", "Can import game key inventory"),
        ]

    def __str__(self):
        return f"Import #{self.pk} for {self.product}"


class GameKey(TimestampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available"
        RESERVED = "reserved"
        ASSIGNED = "assigned"
        DELIVERED = "delivered"
        REDEEMED = "redeemed"
        REFUNDED = "refunded"
        REVOKED = "revoked"
        INVALID = "invalid"

    ALLOCATED_STATUSES = (Status.RESERVED, Status.ASSIGNED)

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="keys")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.AVAILABLE)
    encrypted_key = models.TextField()
    key_fingerprint = models.CharField(max_length=64)
    masked_hint = models.CharField(max_length=32, blank=True)
    reserved_by_item = models.OneToOneField(
        OrderItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="reserved_key"
    )
    claim_token = models.UUIDField(null=True, blank=True)
    reserved_at = models.DateTimeField(null=True, blank=True)
    reservation_expires_at = models.DateTimeField(null=True, blank=True)
    sold_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    import_batch = models.ForeignKey(
        InventoryImportBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="keys"
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "key_fingerprint"], name="uniq_gamekey_product_key_hash"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="available", reserved_by_item__isnull=True)
                    | models.Q(status__in=["reserved", "assigned"], reserved_by_item__isnull=False)
                    | models.Q(
                        status__in=["delivered", "redeemed", "refunded", "revoked", "invalid"]
                    )
                ),
                name="ck_gamekey_status_allocation_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "status"], name="gamekey_product_status_idx"),
            models.Index(
                fields=["status", "reservation_expires_at"], name="gamekey_status_expiry_idx"
            ),
        ]
        permissions = [
            ("reveal_key", "Can reveal a game key's plaintext"),
        ]

    def __str__(self):
        return f"Key #{self.pk} ({self.product}, {self.status})"

    def save(self, *args, allow_unsafe_save=False, **kwargs):
        if self.pk is not None and not allow_unsafe_save:
            raise RuntimeError(
                "GameKey rows must not be mutated via save() once created — use the "
                "compare-and-swap helpers in marketplace.services.inventory instead, "
                "or pass allow_unsafe_save=True for a deliberate admin correction."
            )
        super().save(*args, **kwargs)


class KeyAccessLog(TimestampedModel):
    class Action(models.TextChoices):
        REVEAL = "reveal"
        RATE_LIMITED = "rate_limited"
        DENIED = "denied"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="marketplace_key_access_logs",
    )
    key = models.ForeignKey(GameKey, on_delete=models.PROTECT, related_name="access_logs")
    order = models.ForeignKey(
        Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="key_access_logs"
    )
    action = models.CharField(max_length=15, choices=Action.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"], name="keyaccesslog_user_created_idx"),
        ]

    def __str__(self):
        return f"{self.user}: {self.action} on key #{self.key_id}"
