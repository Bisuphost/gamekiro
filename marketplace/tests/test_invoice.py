from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace import providers
from marketplace.models import MarketplaceSettings, OutboundEmail
from marketplace.providers.stripe_provider import StripeProvider
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import flags as flags_service

from . import factories
from .test_stripe_provider import SECRET, event_body, fake_client, session_object, sign


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["stripe"],
    STRIPE_SECRET_KEY="sk_test_x",
    STRIPE_WEBHOOK_SECRETS=[SECRET],
    MARKETPLACE_SUPPORT_EMAIL="ops@example.test",
)
class InvoiceEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client_fake, self.sessions, _ = fake_client()
        patcher = mock.patch.dict(
            providers.PROVIDERS, {"stripe": StripeProvider(client=self.client_fake)}
        )
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
        factories.make_keys(self.product, 5, self.staff)

    def make_order(self):
        cart_service.add_item(self.buyer, self.product, 1)
        order, payment, intent = checkout_service.create_order(self.buyer, provider_slug="stripe")
        return order, payment, intent.provider_payment_id

    def pay(self, order, session_id):
        body = event_body(
            "evt_paid", "checkout.session.completed",
            session_object(session_id, order.reference, order.total_minor),
        )
        return self.client.post(
            reverse("marketplace:webhook", args=["stripe"]),
            data=body,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sign(body),
        )

    def invoice_emails(self):
        return OutboundEmail.objects.filter(to_email=self.buyer.email, subject__icontains="invoice")

    def test_paying_an_order_queues_exactly_one_invoice_email(self):
        order, _, session_id = self.make_order()
        self.pay(order, session_id)

        self.assertEqual(self.invoice_emails().count(), 1)
        email = self.invoice_emails().get()
        self.assertEqual(email.related_order, order)
        self.assertIn(order.reference, email.body_text)
        self.assertIn("$19.99", email.body_text)

    def test_pending_order_does_not_queue_an_invoice_email(self):
        self.make_order()
        self.assertEqual(self.invoice_emails().count(), 0)

    def test_invoice_page_is_accessible_once_paid(self):
        order, _, session_id = self.make_order()
        self.pay(order, session_id)
        order.refresh_from_db()

        self.client.login(username="buyer", password="testpass123")
        response = self.client.get(reverse("marketplace:order_invoice", args=[order.reference]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.reference)

    def test_invoice_page_redirects_for_unpaid_order(self):
        order, _, _ = self.make_order()

        self.client.login(username="buyer", password="testpass123")
        response = self.client.get(reverse("marketplace:order_invoice", args=[order.reference]))
        self.assertRedirects(
            response, reverse("marketplace:order_detail", args=[order.reference])
        )

    def test_invoice_page_is_not_visible_to_other_users(self):
        order, _, session_id = self.make_order()
        self.pay(order, session_id)
        factories.make_user("someone_else")

        self.client.login(username="someone_else", password="testpass123")
        response = self.client.get(reverse("marketplace:order_invoice", args=[order.reference]))
        self.assertEqual(response.status_code, 404)
