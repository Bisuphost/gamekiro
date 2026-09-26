from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import (
    Entitlement,
    GameKey,
    KeyAccessLog,
    MarketplaceSettings,
    Order,
    WebhookEvent,
)
from marketplace.providers import mock as mock_provider

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class CheckoutHappyPathTests(TestCase):
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
        self.product = factories.make_product(unit_price_minor=1999)
        factories.make_keys(self.product, 3, self.staff)
        self.client.login(username="buyer", password="testpass123")

    def test_full_purchase_flow_delivers_exactly_one_key(self):
        add_response = self.client.post(
            reverse("marketplace:cart_add"),
            {"product_id": self.product.pk, "quantity": 1},
        )
        self.assertEqual(add_response.status_code, 302)

        with self.captureOnCommitCallbacks(execute=True):
            confirm_response = self.client.post(reverse("marketplace:checkout_confirm"))

        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(
            confirm_response,
            reverse("marketplace:mock_checkout", args=[order.reference]),
        )
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(order.items.count(), 1)

        item = order.items.first()
        reserved_key = GameKey.objects.get(reserved_by_item=item)
        self.assertEqual(reserved_key.status, GameKey.Status.RESERVED)

        with self.captureOnCommitCallbacks(execute=True):
            pay_response = self.client.post(
                reverse("marketplace:mock_checkout", args=[order.reference]),
                {"outcome": "succeed"},
            )
        self.assertEqual(pay_response.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertIsNotNone(order.fulfilled_at)

        item.refresh_from_db()
        self.assertIsNotNone(item.delivered_at)

        reserved_key.refresh_from_db()
        self.assertEqual(reserved_key.status, GameKey.Status.ASSIGNED)

        entitlement = Entitlement.objects.get(order_item=item)
        self.assertEqual(entitlement.user, self.buyer)

        reveal_response = self.client.post(
            reverse("marketplace:library_reveal", args=[entitlement.pk])
        )
        self.assertContains(reveal_response, reserved_key.masked_hint[-4:])

        reserved_key.refresh_from_db()
        self.assertEqual(reserved_key.status, GameKey.Status.DELIVERED)
        self.assertTrue(
            KeyAccessLog.objects.filter(
                user=self.buyer, key=reserved_key, action=KeyAccessLog.Action.REVEAL
            ).exists()
        )

    def test_other_user_cannot_access_order_or_key(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
            self.client.post(reverse("marketplace:checkout_confirm"))
        order = Order.objects.get(user=self.buyer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("marketplace:mock_checkout", args=[order.reference]),
                {"outcome": "succeed"},
            )
        entitlement = Entitlement.objects.get(order_item__order=order)

        factories.make_user("intruder")
        self.client.logout()
        self.client.login(username="intruder", password="testpass123")

        order_response = self.client.get(
            reverse("marketplace:order_detail", args=[order.reference])
        )
        self.assertEqual(order_response.status_code, 404)

        reveal_response = self.client.post(
            reverse("marketplace:library_reveal", args=[entitlement.pk])
        )
        self.assertEqual(reveal_response.status_code, 404)

    def test_out_of_stock_rolls_back_cleanly(self):
        empty_product = factories.make_product(game=factories.make_game("No Stock Game"))
        self.client.post(reverse("marketplace:cart_add"), {"product_id": empty_product.pk})

        response = self.client.post(reverse("marketplace:checkout_confirm"), follow=True)
        self.assertContains(response, "sold out")
        self.assertFalse(Order.objects.filter(user=self.buyer).exists())

    def test_duplicate_webhook_delivery_is_idempotent(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
            self.client.post(reverse("marketplace:checkout_confirm"))
        order = Order.objects.get(user=self.buyer)

        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor, order.currency
        )

        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=raw_body,
                content_type="application/json",
                HTTP_X_MOCK_SIGNATURE=signature,
            )
        with self.captureOnCommitCallbacks(execute=True):
            second = self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=raw_body,
                content_type="application/json",
                HTTP_X_MOCK_SIGNATURE=signature,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(WebhookEvent.objects.filter(provider="mock").count(), 1)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(GameKey.objects.filter(reserved_by_item__order=order).count(), 1)

    def test_amount_mismatch_sends_order_to_review(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
            self.client.post(reverse("marketplace:checkout_confirm"))
        order = Order.objects.get(user=self.buyer)

        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor + 100, order.currency
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=raw_body,
                content_type="application/json",
                HTTP_X_MOCK_SIGNATURE=signature,
            )
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEEDS_REVIEW)
        self.assertTrue(order.review_reason)

    def test_bad_signature_is_rejected_and_persists_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
            self.client.post(reverse("marketplace:checkout_confirm"))
        order = Order.objects.get(user=self.buyer)

        raw_body, _ = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor, order.currency
        )
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=raw_body,
            content_type="application/json",
            HTTP_X_MOCK_SIGNATURE="not-a-real-signature",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WebhookEvent.objects.exists())
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
