from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketplace.models import GameKey, MarketplaceSettings, Order
from marketplace.providers import mock as mock_provider
from marketplace.services import inventory as inventory_service

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class ReservationExpiryTests(TestCase):
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
        factories.make_keys(self.product, 1, self.staff)
        self.client.login(username="buyer", password="testpass123")

    def _place_order(self):
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        self.client.post(reverse("marketplace:checkout_confirm"))
        return Order.objects.get(user=self.buyer)

    def test_sweep_expires_order_then_releases_key(self):
        order = self._place_order()
        Order.objects.filter(pk=order.pk).update(
            reservation_expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )

        expired_count, released_count = inventory_service.sweep_expired_reservations()

        self.assertEqual(expired_count, 1)
        self.assertEqual(released_count, 1)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.EXPIRED)

        key = GameKey.objects.get(product=self.product)
        self.assertEqual(key.status, GameKey.Status.AVAILABLE)
        self.assertIsNone(key.reserved_by_item_id)

    def test_sweep_does_not_touch_unexpired_orders(self):
        order = self._place_order()
        expired_count, released_count = inventory_service.sweep_expired_reservations()
        self.assertEqual(expired_count, 0)
        self.assertEqual(released_count, 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

    def test_late_payment_after_expiry_recovers_when_stock_exists(self):
        order = self._place_order()
        Order.objects.filter(pk=order.pk).update(
            reservation_expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )
        inventory_service.sweep_expired_reservations()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.EXPIRED)

        factories.make_keys(self.product, 1, self.staff, prefix="LATE")

        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor, order.currency
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
        self.assertEqual(order.status, Order.Status.FULFILLED)
        item = order.items.first()
        self.assertIsNotNone(item.delivered_at)

    def test_late_payment_after_expiry_reviews_when_out_of_stock(self):
        order = self._place_order()
        Order.objects.filter(pk=order.pk).update(
            reservation_expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )
        inventory_service.sweep_expired_reservations()

        other_buyer = factories.make_user("other_buyer")
        self.client.logout()
        self.client.login(username="other_buyer", password="testpass123")
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("marketplace:checkout_confirm"))
        competing_order = Order.objects.get(user=other_buyer)
        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded",
            competing_order,
            competing_order.total_minor,
            competing_order.currency,
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=raw_body,
                content_type="application/json",
                HTTP_X_MOCK_SIGNATURE=signature,
            )
        competing_order.refresh_from_db()
        self.assertEqual(competing_order.status, Order.Status.FULFILLED)

        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor, order.currency
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
