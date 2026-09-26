from django.conf import settings
from django.db import models
from taggit.managers import TaggableManager

from core.models import TimestampedModel

from .catalog import Product
from .orders import Order


class Coupon(TimestampedModel):
    class Kind(models.TextChoices):
        PERCENT = "percent"
        FIXED = "fixed"

    code = models.CharField(max_length=32, unique=True)
    kind = models.CharField(max_length=7, choices=Kind.choices)
    value = models.PositiveIntegerField()
    max_redemptions = models.PositiveIntegerField(null=True, blank=True)
    max_per_user = models.PositiveIntegerField(default=1)
    min_subtotal_minor = models.PositiveBigIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    products = models.ManyToManyField(Product, blank=True, related_name="coupons")
    tags = TaggableManager(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code


class CouponRedemption(TimestampedModel):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coupon_redemptions"
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="coupon_redemptions")
    amount_minor = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["coupon", "order"], name="uniq_couponredemption_coupon_order"
            ),
        ]

    def __str__(self):
        return f"{self.coupon}: {self.user}"
