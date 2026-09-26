from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from marketplace.models import Coupon, MarketplaceSettings, Order
from marketplace.models.orders import order_transition
from marketplace.services import coupons as coupons_service
from marketplace.services import inventory as inventory_service
from marketplace.services import keys as keys_service
from marketplace.services import pricing as pricing_service

from . import factories


class PricingTests(TestCase):
    def test_price_line_items_computes_subtotal_and_total(self):
        product = factories.make_product(unit_price_minor=1000)
        priced = pricing_service.price_line_items([(product, 3)])
        self.assertEqual(priced["subtotal_minor"], 3000)
        self.assertEqual(priced["total_minor"], 3000)
        self.assertEqual(priced["discount_minor"], 0)

    def test_percent_coupon_discount_never_exceeds_subtotal(self):
        coupon = Coupon.objects.create(code="HUGE", kind=Coupon.Kind.PERCENT, value=500)
        discount = coupons_service.compute_discount(coupon, 1000)
        self.assertEqual(discount, 1000)

    def test_fixed_coupon_discount_never_exceeds_subtotal(self):
        coupon = Coupon.objects.create(code="FIXED", kind=Coupon.Kind.FIXED, value=99999)
        discount = coupons_service.compute_discount(coupon, 500)
        self.assertEqual(discount, 500)
        total = pricing_service.price_line_items(
            [(factories.make_product(unit_price_minor=500), 1)], coupon=coupon
        )
        self.assertGreaterEqual(total["total_minor"], 0)


class CouponValidationTests(TestCase):
    def setUp(self):
        self.user = factories.make_user()

    def test_expired_coupon_is_rejected(self):
        Coupon.objects.create(
            code="OLD",
            kind=Coupon.Kind.PERCENT,
            value=10,
            ends_at=timezone.now() - timezone.timedelta(days=1),
        )
        with self.assertRaises(coupons_service.CouponInvalid):
            coupons_service.validate_coupon("OLD", self.user, 1000)

    def test_below_minimum_subtotal_is_rejected(self):
        Coupon.objects.create(
            code="MIN50", kind=Coupon.Kind.PERCENT, value=10, min_subtotal_minor=5000
        )
        with self.assertRaises(coupons_service.CouponInvalid):
            coupons_service.validate_coupon("MIN50", self.user, 1000)

    def test_per_user_redemption_limit_is_enforced(self):
        product = factories.make_product()
        coupon = Coupon.objects.create(
            code="ONEUSE", kind=Coupon.Kind.PERCENT, value=10, max_per_user=1
        )
        order = Order.objects.create(user=self.user, currency="USD", total_minor=100)
        coupons_service.redeem_coupon(coupon, self.user, order, 10)
        with self.assertRaises(coupons_service.CouponInvalid):
            coupons_service.validate_coupon("ONEUSE", self.user, 10000, product_ids=[product.pk])


class OrderTransitionTests(TestCase):
    def test_cas_rejects_transition_from_wrong_source_state(self):
        user = factories.make_user()
        order = Order.objects.create(
            user=user, currency="USD", total_minor=100, status=Order.Status.FULFILLED
        )
        transitioned = order_transition(order, to=Order.Status.PAID)
        self.assertFalse(transitioned)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)

    def test_cas_succeeds_from_valid_source_state(self):
        user = factories.make_user()
        order = Order.objects.create(
            user=user, currency="USD", total_minor=100, status=Order.Status.PENDING_PAYMENT
        )
        transitioned = order_transition(order, to=Order.Status.PAID)
        self.assertTrue(transitioned)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_double_transition_is_idempotent_no_op(self):
        user = factories.make_user()
        order = Order.objects.create(
            user=user, currency="USD", total_minor=100, status=Order.Status.PENDING_PAYMENT
        )
        first = order_transition(order, to=Order.Status.PAID)
        second = order_transition(order, to=Order.Status.PAID)
        self.assertTrue(first)
        self.assertFalse(second)


@override_settings(MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class KeyEncryptionTests(TestCase):
    def test_roundtrip_and_fingerprint_and_mask(self):
        plaintext = "ABCDE-FGHIJ-KLMNO"
        ciphertext = keys_service.encrypt(plaintext)
        self.assertNotIn(plaintext, ciphertext)
        self.assertEqual(keys_service.decrypt(ciphertext), plaintext)

        fingerprint_a = keys_service.fingerprint(plaintext)
        fingerprint_b = keys_service.fingerprint(" abcde-fghij-klmno ")
        self.assertEqual(fingerprint_a, fingerprint_b)

        masked = keys_service.mask(plaintext)
        self.assertTrue(masked.endswith("MNO"))
        self.assertNotIn("ABCDE", masked)


@override_settings(MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class ImportKeysTests(TestCase):
    def test_duplicate_key_within_batch_is_only_imported_once(self):
        cache.clear()
        MarketplaceSettings.objects.update_or_create(pk=1, defaults={"marketplace_enabled": True})
        staff = factories.make_user("importer")
        product = factories.make_product()
        batch = inventory_service.import_keys(
            product, ["DUP-KEY-1", "DUP-KEY-1", "NEW-KEY-2"], staff
        )
        self.assertEqual(batch.imported_count, 2)
        self.assertEqual(batch.duplicate_count, 1)
