from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from marketplace import providers
from marketplace.models import MarketplaceSettings, Order, OrderAuditLog
from marketplace.providers.base import PaymentIntent
from marketplace.providers.mock import MockProvider
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import flags as flags_service
from marketplace.services import payments as payments_service
from marketplace.services import ratelimit as ratelimit_service
from marketplace.views.utils import client_ip

from . import factories


class ClientIpTests(TestCase):
    def _request(self, remote_addr="203.0.113.9", xff=None):
        rf = RequestFactory()
        extra = {"REMOTE_ADDR": remote_addr}
        if xff is not None:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        return rf.get("/", **extra)

    @override_settings(MARKETPLACE_TRUSTED_PROXY_COUNT=0)
    def test_default_ignores_client_supplied_forwarded_header(self):
        request = self._request(remote_addr="203.0.113.9", xff="1.2.3.4")
        self.assertEqual(client_ip(request), "203.0.113.9")

    @override_settings(MARKETPLACE_TRUSTED_PROXY_COUNT=0)
    def test_default_with_no_forwarded_header_uses_remote_addr(self):
        request = self._request(remote_addr="203.0.113.9")
        self.assertEqual(client_ip(request), "203.0.113.9")

    @override_settings(MARKETPLACE_TRUSTED_PROXY_COUNT=1)
    def test_one_trusted_proxy_uses_the_rightmost_forwarded_entry(self):
        # attacker, real-client, trusted-proxy-appended
        request = self._request(xff="9.9.9.9, 5.5.5.5, 6.6.6.6")
        self.assertEqual(client_ip(request), "6.6.6.6")

    @override_settings(MARKETPLACE_TRUSTED_PROXY_COUNT=2)
    def test_two_trusted_proxies_skips_two_from_the_right(self):
        request = self._request(xff="9.9.9.9, 5.5.5.5, 6.6.6.6")
        self.assertEqual(client_ip(request), "5.5.5.5")

    @override_settings(MARKETPLACE_TRUSTED_PROXY_COUNT=5)
    def test_fewer_hops_than_configured_falls_back_to_remote_addr(self):
        request = self._request(remote_addr="203.0.113.9", xff="1.1.1.1")
        self.assertEqual(client_ip(request), "203.0.113.9")

    @override_settings(MARKETPLACE_TRUSTED_PROXY_COUNT=1)
    def test_attacker_cannot_inject_extra_hops_to_forge_the_trusted_entry(self):
        # An attacker padding X-Forwarded-For with fake entries still only controls
        # the header itself; the trusted proxy is assumed to APPEND its hop, so the
        # rightmost entry is always the one nobody but that proxy could have written.
        request = self._request(xff="attacker-injected-1, attacker-injected-2, 7.7.7.7")
        self.assertEqual(client_ip(request), "7.7.7.7")


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class OrderVelocityTests(TestCase):
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
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product(unit_price_minor=500)
        factories.make_keys(self.product, 30, self.staff)

    def _buy_one(self):
        cart_service.add_item(self.buyer, self.product, 1)
        return checkout_service.create_order(self.buyer, provider_slug="mock")

    @override_settings(MARKETPLACE_ALLOW_MOCK=True, PAYMENT_PROVIDERS_ENABLED=["mock"])
    @override_settings(MARKETPLACE_MAX_ORDERS_PER_DAY=3)
    def test_orders_beyond_the_daily_cap_are_refused(self):
        for _ in range(3):
            self._buy_one()
        with self.assertRaises(checkout_service.CheckoutError):
            self._buy_one()
        self.assertEqual(Order.objects.filter(user=self.buyer).count(), 3)

    @override_settings(MARKETPLACE_ALLOW_MOCK=True, PAYMENT_PROVIDERS_ENABLED=["mock"])
    @override_settings(MARKETPLACE_MAX_ORDERS_PER_DAY=0)
    def test_zero_disables_the_cap(self):
        for _ in range(5):
            self._buy_one()
        self.assertEqual(Order.objects.filter(user=self.buyer).count(), 5)

    @override_settings(MARKETPLACE_ALLOW_MOCK=True, PAYMENT_PROVIDERS_ENABLED=["mock"])
    @override_settings(MARKETPLACE_MAX_ORDERS_PER_DAY=2)
    def test_cap_is_scoped_per_user(self):
        other = factories.make_user("other")
        cart_service.add_item(other, self.product, 1)
        for _ in range(2):
            self._buy_one()
        checkout_service.create_order(other, provider_slug="mock")
        self.assertEqual(Order.objects.filter(user=other).count(), 1)


class FakeDeclineProvider(MockProvider):
    slug = "stripe"
    display_name = "Fake decliner"
    min_amount_minor = 50

    def create_payment(self, order, payment, urls):
        return PaymentIntent(
            provider_payment_id=f"cs_{payment.idempotency_key}",
            redirect_url="https://pay.example.test/x",
            expires_at=timezone.now() + timedelta(minutes=31),
        )


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["stripe"],
)
class PaymentFailureLockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeDeclineProvider()
        patcher = mock.patch.dict(providers.PROVIDERS, {"stripe": self.fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        MarketplaceSettings.objects.update_or_create(
            pk=1,
            defaults={
                "marketplace_enabled": True,
                "purchases_enabled": True,
                "fulfillment_enabled": True,
                "stripe_enabled": True,
            },
        )
        flags_service.invalidate()
        self.staff = factories.make_user("staff")
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product(unit_price_minor=1999)
        factories.make_keys(self.product, 20, self.staff)

    def _order(self, ip="198.51.100.1"):
        cart_service.add_item(self.buyer, self.product, 1)
        return checkout_service.create_order(self.buyer, provider_slug="stripe", ip=ip)

    def _decline(self, order, session_id, ip="198.51.100.1"):
        from marketplace.services import payments as payments_service

        event = self.fake.normalize_event(
            {
                "event_id": f"evt-{session_id}",
                "event_type": "attempt_failed",
                "order_reference": order.reference,
                "provider_payment_id": session_id,
                "amount_minor": order.total_minor,
                "currency": order.currency,
            }
        )
        return payments_service.apply_payment_confirmation(event)

    @override_settings(
        MARKETPLACE_MAX_PAYMENT_FAILURES=3, MARKETPLACE_PAYMENT_FAILURE_COOLDOWN_MINUTES=30
    )
    def test_repeated_declines_lock_out_further_checkout_by_user(self):
        for _ in range(3):
            order, payment, intent = self._order()
            self._decline(order, intent.provider_payment_id)

        with self.assertRaises(checkout_service.CheckoutError):
            self._order()

    @override_settings(
        MARKETPLACE_MAX_PAYMENT_FAILURES=3, MARKETPLACE_PAYMENT_FAILURE_COOLDOWN_MINUTES=30
    )
    def test_lockout_also_blocks_a_different_user_from_the_same_ip(self):
        attacker_ip = "198.51.100.77"
        for _ in range(3):
            order, payment, intent = self._order(ip=attacker_ip)
            self._decline(order, intent.provider_payment_id)

        other = factories.make_user("other")
        cart_service.add_item(other, self.product, 1)
        with self.assertRaises(checkout_service.CheckoutError):
            checkout_service.create_order(other, provider_slug="stripe", ip=attacker_ip)

    @override_settings(
        MARKETPLACE_MAX_PAYMENT_FAILURES=3, MARKETPLACE_PAYMENT_FAILURE_COOLDOWN_MINUTES=30
    )
    def test_lockout_does_not_affect_an_unrelated_user_on_a_different_ip(self):
        for _ in range(3):
            order, payment, intent = self._order(ip="198.51.100.1")
            self._decline(order, intent.provider_payment_id)

        other = factories.make_user("other")
        cart_service.add_item(other, self.product, 1)
        order, payment, intent = checkout_service.create_order(
            other, provider_slug="stripe", ip="198.51.100.200"
        )
        self.assertIsNotNone(intent)

    @override_settings(MARKETPLACE_MAX_PAYMENT_FAILURES=0)
    def test_zero_disables_the_lockout(self):
        for _ in range(10):
            order, payment, intent = self._order()
            self._decline(order, intent.provider_payment_id)
        order, payment, intent = self._order()
        self.assertIsNotNone(intent)

    @override_settings(
        MARKETPLACE_MAX_PAYMENT_FAILURES=2, MARKETPLACE_PAYMENT_FAILURE_COOLDOWN_MINUTES=30
    )
    def test_lockout_also_blocks_resuming_payment_on_an_existing_order(self):
        order1, _, intent1 = self._order()
        order2, _, intent2 = self._order()
        order3, _, intent3 = self._order()

        self._decline(order1, intent1.provider_payment_id)
        self._decline(order2, intent2.provider_payment_id)

        with self.assertRaises(payments_service.PaymentError):
            payments_service.start_or_resume_payment(order3, "stripe", ip="198.51.100.1")

    def test_expired_attempts_do_not_count_as_failures(self):
        with override_settings(
            MARKETPLACE_MAX_PAYMENT_FAILURES=2, MARKETPLACE_PAYMENT_FAILURE_COOLDOWN_MINUTES=30
        ):
            for _ in range(5):
                order, payment, intent = self._order()
                event = self.fake.normalize_event(
                    {
                        "event_id": f"exp-{intent.provider_payment_id}",
                        "event_type": "attempt_expired",
                        "order_reference": order.reference,
                        "provider_payment_id": intent.provider_payment_id,
                        "amount_minor": order.total_minor,
                        "currency": order.currency,
                    }
                )
                payments_service.apply_payment_confirmation(event)
            order, payment, intent = self._order()
            self.assertIsNotNone(intent)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class ManualReviewHoldTests(TestCase):
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
        self.buyer = factories.make_user("buyer")

    def _pay(self, order):
        from marketplace.providers import mock as mock_provider

        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor, order.currency
        )
        from marketplace.services import webhooks as webhooks_service

        return webhooks_service.handle("mock", raw_body, {"X-Mock-Signature": signature})

    @override_settings(
        MARKETPLACE_ALLOW_MOCK=True,
        PAYMENT_PROVIDERS_ENABLED=["mock"],
        MARKETPLACE_HIGH_VALUE_REVIEW_MINOR=10000,
        MARKETPLACE_FIRST_ORDER_REVIEW_MINOR=0,
    )
    def test_high_value_order_is_held_instead_of_auto_fulfilled(self):
        product = factories.make_product(unit_price_minor=15000)
        factories.make_keys(product, 1, self.staff)
        cart_service.add_item(self.buyer, product, 1)
        order, payment, intent = checkout_service.create_order(self.buyer, provider_slug="mock")

        with self.captureOnCommitCallbacks(execute=True):
            self._pay(order)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEEDS_REVIEW)
        self.assertTrue(order.review_reason)
        self.assertTrue(
            OrderAuditLog.objects.filter(order=order, action="fraud_review_hold").exists()
        )

    @override_settings(
        MARKETPLACE_ALLOW_MOCK=True,
        PAYMENT_PROVIDERS_ENABLED=["mock"],
        MARKETPLACE_HIGH_VALUE_REVIEW_MINOR=0,
        MARKETPLACE_FIRST_ORDER_REVIEW_MINOR=5000,
    )
    def test_first_order_above_threshold_is_held(self):
        product = factories.make_product(unit_price_minor=6000)
        factories.make_keys(product, 1, self.staff)
        cart_service.add_item(self.buyer, product, 1)
        order, payment, intent = checkout_service.create_order(self.buyer, provider_slug="mock")

        with self.captureOnCommitCallbacks(execute=True):
            self._pay(order)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEEDS_REVIEW)

    @override_settings(
        MARKETPLACE_ALLOW_MOCK=True,
        PAYMENT_PROVIDERS_ENABLED=["mock"],
        MARKETPLACE_HIGH_VALUE_REVIEW_MINOR=0,
        MARKETPLACE_FIRST_ORDER_REVIEW_MINOR=5000,
    )
    def test_second_order_above_threshold_is_not_held_once_a_prior_order_settled(self):
        cheap = factories.make_product(
            game=factories.make_game("Cheap Game"), unit_price_minor=500, slug="cheap-first-order"
        )
        pricey = factories.make_product(
            game=factories.make_game("Pricey Game"),
            unit_price_minor=6000,
            slug="pricey-second-order",
        )
        factories.make_keys(cheap, 1, self.staff, prefix="CHEAP")
        factories.make_keys(pricey, 1, self.staff, prefix="PRICEY")

        cart_service.add_item(self.buyer, cheap, 1)
        first_order, _, _ = checkout_service.create_order(self.buyer, provider_slug="mock")
        with self.captureOnCommitCallbacks(execute=True):
            self._pay(first_order)
        first_order.refresh_from_db()
        self.assertEqual(first_order.status, Order.Status.FULFILLED)

        cart_service.add_item(self.buyer, pricey, 1)
        second_order, _, _ = checkout_service.create_order(self.buyer, provider_slug="mock")
        with self.captureOnCommitCallbacks(execute=True):
            self._pay(second_order)
        second_order.refresh_from_db()
        self.assertEqual(second_order.status, Order.Status.FULFILLED)

    @override_settings(
        MARKETPLACE_ALLOW_MOCK=True,
        PAYMENT_PROVIDERS_ENABLED=["mock"],
        MARKETPLACE_HIGH_VALUE_REVIEW_MINOR=10000,
        MARKETPLACE_FIRST_ORDER_REVIEW_MINOR=0,
    )
    def test_low_value_order_is_not_held(self):
        product = factories.make_product(unit_price_minor=500)
        factories.make_keys(product, 1, self.staff)
        cart_service.add_item(self.buyer, product, 1)
        order, payment, intent = checkout_service.create_order(self.buyer, provider_slug="mock")

        with self.captureOnCommitCallbacks(execute=True):
            self._pay(order)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)

    @override_settings(
        MARKETPLACE_ALLOW_MOCK=True,
        PAYMENT_PROVIDERS_ENABLED=["mock"],
        MARKETPLACE_HIGH_VALUE_REVIEW_MINOR=10000,
        MARKETPLACE_FIRST_ORDER_REVIEW_MINOR=0,
    )
    def test_staff_can_release_a_held_order_via_the_existing_redrive_action(self):
        product = factories.make_product(unit_price_minor=15000)
        factories.make_keys(product, 1, self.staff)
        cart_service.add_item(self.buyer, product, 1)
        order, payment, intent = checkout_service.create_order(self.buyer, provider_slug="mock")
        with self.captureOnCommitCallbacks(execute=True):
            self._pay(order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEEDS_REVIEW)

        from marketplace.services import fulfillment as fulfillment_service

        with self.captureOnCommitCallbacks(execute=True):
            fulfillment_service.fulfill_order(order.pk)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class RatelimitPeekTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_peek_does_not_increment(self):
        ratelimit_service.peek("action", "id", 60)
        ratelimit_service.peek("action", "id", 60)
        self.assertEqual(ratelimit_service.peek("action", "id", 60), 0)

    def test_peek_reflects_hits(self):
        ratelimit_service.hit("action", "id", 100, 60)
        ratelimit_service.hit("action", "id", 100, 60)
        self.assertEqual(ratelimit_service.peek("action", "id", 60), 2)

    def test_peek_is_scoped_to_the_current_window(self):
        now = timezone.now()
        ratelimit_service.hit("action", "id", 100, 60, now=now)
        later = now + timedelta(seconds=61)
        self.assertEqual(ratelimit_service.peek("action", "id", 60, now=later), 0)
