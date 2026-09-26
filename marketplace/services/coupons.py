from django.utils import timezone

from marketplace.models import Coupon, CouponRedemption


class CouponInvalid(Exception):
    pass


def compute_discount(coupon, subtotal_minor):
    if coupon.kind == Coupon.Kind.PERCENT:
        return min((subtotal_minor * coupon.value) // 100, subtotal_minor)
    return min(coupon.value, subtotal_minor)


def validate_coupon(code, user, subtotal_minor, product_ids=None):
    now = timezone.now()
    try:
        coupon = Coupon.objects.get(code__iexact=code.strip(), is_active=True)
    except Coupon.DoesNotExist as exc:
        raise CouponInvalid("Coupon not found.") from exc

    if coupon.starts_at and coupon.starts_at > now:
        raise CouponInvalid("Coupon is not active yet.")
    if coupon.ends_at and coupon.ends_at < now:
        raise CouponInvalid("Coupon has expired.")
    if subtotal_minor < coupon.min_subtotal_minor:
        raise CouponInvalid("Order subtotal is below the coupon's minimum.")
    if coupon.max_redemptions is not None and coupon.redemptions.count() >= coupon.max_redemptions:
        raise CouponInvalid("Coupon has reached its redemption limit.")
    if user is not None and getattr(user, "is_authenticated", False):
        user_redemptions = coupon.redemptions.filter(user=user).count()
        if user_redemptions >= coupon.max_per_user:
            raise CouponInvalid("You have already used this coupon.")

    scoped_products = coupon.products.all()
    if scoped_products.exists():
        if product_ids is None or not scoped_products.filter(pk__in=product_ids).exists():
            raise CouponInvalid("Coupon does not apply to items in your cart.")

    return coupon


def redeem_coupon(coupon, user, order, amount_minor):
    redemption, _ = CouponRedemption.objects.get_or_create(
        coupon=coupon, order=order, defaults={"user": user, "amount_minor": amount_minor}
    )
    return redemption
