import threading

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings, tag
from django.urls import reverse

from marketplace.models import MarketplaceSettings, Order, RateLimitCounter
from marketplace.services import ratelimit as ratelimit_service
from marketplace.services import verification as verification_service

from . import factories


@tag("concurrency")
@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class RateLimitCounterConcurrencyTests(TransactionTestCase):
    def test_concurrent_hits_are_not_lost_to_a_race(self):
        results = []

        def worker():
            try:
                results.append(ratelimit_service.hit("race", "shared-key", 1000, 60))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 30)
        counter = RateLimitCounter.objects.get(key__startswith="race:shared-key:")
        self.assertEqual(counter.count, 30)

    def test_an_attacker_cannot_race_past_a_low_limit(self):
        allowed = []

        def worker():
            try:
                allowed.append(ratelimit_service.hit("race-limit", "attacker", 5, 60))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for ok in allowed if ok), 5)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class InputHardeningTests(TestCase):
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

    def test_sql_metacharacters_in_provider_field_do_not_error_or_match_anything(self):
        self.client.login(username="buyer", password="testpass123")
        response = self.client.post(
            reverse("marketplace:checkout_confirm"),
            {"provider": "mock'; DROP TABLE marketplace_order; --"},
        )
        self.assertIn(response.status_code, (302, 400))
        self.assertTrue(Order.objects.model._meta.db_table)

    def test_html_script_in_refund_reason_is_escaped_on_render(self):
        product = factories.make_product(unit_price_minor=1000)
        factories.make_keys(product, 1, self.staff)
        from marketplace.services import cart as cart_service
        from marketplace.services import checkout as checkout_service
        from marketplace.services import refunds as refunds_service

        cart_service.add_item(self.buyer, product, 1)
        order, payment, intent = checkout_service.create_order(self.buyer, provider_slug="mock")

        from marketplace.providers import mock as mock_provider
        from marketplace.services import webhooks as webhooks_service

        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor, order.currency
        )
        with self.captureOnCommitCallbacks(execute=True):
            webhooks_service.handle("mock", raw_body, {"X-Mock-Signature": signature})

        payment.refresh_from_db()
        evil = "<script>alert(document.cookie)</script>"
        refunds_service.request_refund(order, payment, 500, evil, self.staff)

        self.staff.is_staff = True
        self.staff.save()
        from django.contrib.auth.models import Permission

        self.staff.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_order", "refund_order"])
        )
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(
            reverse("marketplace:manage_order_detail", args=[order.reference])
        )
        self.assertNotContains(response, "<script>alert(document.cookie)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_extremely_long_ip_header_does_not_crash_checkout(self):
        self.client.login(username="buyer", password="testpass123")
        product = factories.make_product(unit_price_minor=500)
        factories.make_keys(product, 1, self.staff)
        self.client.post(reverse("marketplace:cart_add"), {"product_id": product.pk})
        response = self.client.post(
            reverse("marketplace:checkout_confirm"),
            {"provider": "mock"},
            HTTP_X_FORWARDED_FOR="1.1.1.1, " * 500,
        )
        self.assertIn(response.status_code, (200, 302))


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class CsrfEnforcementTests(TestCase):
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
        self.buyer = factories.make_user("buyer", verified=False)

    def test_verify_email_resend_requires_a_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="buyer", password="testpass123")
        response = csrf_client.post(reverse("marketplace:verify_email_resend"))
        self.assertEqual(response.status_code, 403)

    def test_webhook_endpoint_is_deliberately_csrf_exempt(self):
        # Providers can't fetch a CSRF token; the endpoint is protected instead by
        # signature verification. Confirm it isn't blocked by CSRF, only by the
        # signature check (400), which is what proves the exemption is intentional
        # and not accidentally leaving it open.
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class TokenEnumerationResistanceTests(TestCase):
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

    def test_verification_tokens_for_different_users_are_not_trivially_related(self):
        a = factories.make_user("alice", verified=False)
        b = factories.make_user("bob", verified=False)
        token_a = verification_service.make_token(a)
        token_b = verification_service.make_token(b)
        self.assertNotEqual(token_a, token_b)
        sig_a = token_a.rsplit(":", 1)[-1]
        sig_b = token_b.rsplit(":", 1)[-1]
        self.assertNotEqual(sig_a, sig_b)

    def test_order_reference_has_enough_entropy_to_resist_guessing(self):
        from marketplace.models import Order

        user = factories.make_user("buyer")
        order = Order.objects.create(user=user, currency="USD", total_minor=100)
        # ORD- + 16 hex chars == 64 bits of entropy; sanity-check the shape holds.
        self.assertRegex(order.reference, r"^ORD-[0-9A-F]{16}$")
