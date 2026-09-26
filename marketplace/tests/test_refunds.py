from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import GameKey, MarketplaceSettings, Order, Refund
from marketplace.providers import mock as mock_provider

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class RefundAndChargebackTests(TestCase):
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
        self.finance = factories.make_user("finance")
        self.finance.groups.add(Group.objects.get(name="Marketplace Finance"))
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product(unit_price_minor=1000)
        factories.make_keys(self.product, 1, self.staff)

        self.client.login(username="buyer", password="testpass123")
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        self.client.post(reverse("marketplace:checkout_confirm"))
        self.order = Order.objects.get(user=self.buyer)

        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", self.order, self.order.total_minor, self.order.currency
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=raw_body,
                content_type="application/json",
                HTTP_X_MOCK_SIGNATURE=signature,
            )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)

    def test_full_refund_revokes_key_and_marks_order_refunded(self):
        self.client.logout()
        self.client.login(username="finance", password="testpass123")

        response = self.client.post(
            reverse("marketplace:manage_order_refund", args=[self.order.reference]),
            {"amount_minor": self.order.total_minor, "reason": "customer request"},
        )
        self.assertRedirects(
            response, reverse("marketplace:manage_order_detail", args=[self.order.reference])
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.REFUNDED)

        key = GameKey.objects.get(product=self.product)
        self.assertEqual(key.status, GameKey.Status.REFUNDED)
        self.assertIsNotNone(key.revoked_at)

        self.assertTrue(
            Refund.objects.filter(order=self.order, status=Refund.Status.COMPLETED).exists()
        )

    def test_refund_never_returns_key_to_available_pool(self):
        self.client.logout()
        self.client.login(username="finance", password="testpass123")
        self.client.post(
            reverse("marketplace:manage_order_refund", args=[self.order.reference]),
            {"amount_minor": self.order.total_minor, "reason": "test"},
        )
        key = GameKey.objects.get(product=self.product)
        self.assertNotEqual(key.status, GameKey.Status.AVAILABLE)

    def test_customer_cannot_initiate_refund(self):
        response = self.client.post(
            reverse("marketplace:manage_order_refund", args=[self.order.reference]),
            {"amount_minor": self.order.total_minor},
        )
        self.assertEqual(response.status_code, 403)

    def test_chargeback_webhook_revokes_key_and_never_auto_refunds(self):
        raw_body, signature = mock_provider.build_signed_event(
            "dispute_created", self.order, self.order.total_minor, self.order.currency
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=raw_body,
                content_type="application/json",
                HTTP_X_MOCK_SIGNATURE=signature,
            )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CHARGEBACK)

        key = GameKey.objects.get(product=self.product)
        self.assertEqual(key.status, GameKey.Status.REVOKED)
        self.assertFalse(Refund.objects.filter(order=self.order).exists())
