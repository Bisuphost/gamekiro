from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import Coupon, MarketplaceSettings, Order
from marketplace.services import coupons as coupons_service

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class CouponAbuseTests(TestCase):
    def setUp(self):
        cache.clear()
        MarketplaceSettings.objects.update_or_create(
            pk=1,
            defaults={
                "marketplace_enabled": True,
                "purchases_enabled": True,
                "fulfillment_enabled": True,
            },
        )
        self.staff = factories.make_user("staff")
        self.product_a = factories.make_product(
            game=factories.make_game("Game A"), unit_price_minor=1000
        )
        self.product_b = factories.make_product(
            game=factories.make_game("Game B"), unit_price_minor=1000
        )
        factories.make_keys(self.product_a, 5, self.staff)
        factories.make_keys(self.product_b, 5, self.staff)

    def _checkout_with_coupon(self, username, product, code):
        self.client.logout()
        self.client.login(username=username, password="testpass123")
        self.client.post(reverse("marketplace:cart_add"), {"product_id": product.pk})
        self.client.post(reverse("marketplace:coupon_validate"), {"code": code})
        self.client.post(reverse("marketplace:checkout_confirm"))
        return Order.objects.filter(user__username=username).order_by("-created_at").first()

    def test_global_redemption_limit_is_enforced(self):
        Coupon.objects.create(code="LIMITED", kind=Coupon.Kind.PERCENT, value=10, max_redemptions=1)
        factories.make_user("buyer_one")
        factories.make_user("buyer_two")

        order_one = self._checkout_with_coupon("buyer_one", self.product_a, "LIMITED")
        self.assertIsNotNone(order_one.coupon)
        self.assertEqual(order_one.discount_minor, 100)

        order_two = self._checkout_with_coupon("buyer_two", self.product_a, "LIMITED")
        self.assertIsNone(order_two.coupon)
        self.assertEqual(order_two.discount_minor, 0)

    def test_coupon_scoped_to_one_product_does_not_apply_to_another(self):
        coupon = Coupon.objects.create(code="SCOPED", kind=Coupon.Kind.PERCENT, value=50)
        coupon.products.add(self.product_a)
        factories.make_user("scoped_buyer")

        with self.assertRaises(coupons_service.CouponInvalid):
            coupons_service.validate_coupon(
                "SCOPED", None, self.product_b.unit_price_minor, product_ids=[self.product_b.pk]
            )

        allowed = coupons_service.validate_coupon(
            "SCOPED", None, self.product_a.unit_price_minor, product_ids=[self.product_a.pk]
        )
        self.assertEqual(allowed.code, "SCOPED")

    def test_inactive_coupon_is_rejected(self):
        Coupon.objects.create(code="OFF", kind=Coupon.Kind.PERCENT, value=10, is_active=False)
        with self.assertRaises(coupons_service.CouponInvalid):
            coupons_service.validate_coupon("OFF", None, 1000)
