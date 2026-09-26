from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from marketplace.models import MarketplaceSettings, Order

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class SecurityTests(TestCase):
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
        self.product = factories.make_product(unit_price_minor=2000)
        factories.make_keys(self.product, 2, self.staff)

    def test_state_changing_post_without_csrf_token_is_rejected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="buyer", password="testpass123")
        response = csrf_client.post(
            reverse("marketplace:cart_add"), {"product_id": self.product.pk}
        )
        self.assertEqual(response.status_code, 403)

    def test_posted_price_is_ignored_server_computes_total(self):
        self.client.login(username="buyer", password="testpass123")
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        self.client.post(
            reverse("marketplace:checkout_confirm"),
            {
                "total_minor": "1",
                "unit_price_minor": "1",
                "amount": "0.01",
            },
        )
        order = Order.objects.get(user=self.buyer)
        self.assertEqual(order.total_minor, self.product.unit_price_minor)

    def test_product_description_html_is_escaped_not_executed(self):
        self.product.description = "<script>alert(document.cookie)</script>"
        self.product.save()
        response = self.client.get(reverse("marketplace:product_detail", args=[self.product.slug]))
        self.assertNotContains(response, "<script>alert(document.cookie)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_webhook_without_signature_header_is_rejected(self):
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b'{"event_type": "payment_succeeded"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_payment_provider_returns_404(self):
        response = self.client.post(
            reverse("marketplace:webhook", args=["totally-fake-provider"]),
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
