from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketplace import providers
from marketplace.models import MarketplaceSettings, Order, Payment
from marketplace.providers.base import PaymentIntent, ProviderError
from marketplace.providers.mock import MockProvider
from marketplace.services import checkout as checkout_service
from marketplace.services import flags as flags_service
from marketplace.services import payments as payments_service

from . import factories


class FakeStripe(MockProvider):
    slug = "stripe"
    display_name = "Card (fake)"
    min_amount_minor = 50

    def __init__(self):
        self.created = []
        self.cancelled = []
        self.fail_create = False
        self.fail_cancel = False

    def create_payment(self, order, payment, urls):
        if self.fail_create:
            raise ProviderError("provider down", code="network")
        self.created.append(payment.idempotency_key)
        return PaymentIntent(
            provider_payment_id=f"cs_{payment.idempotency_key}",
            redirect_url=f"https://pay.example.test/{payment.attempt}",
            expires_at=timezone.now() + timedelta(minutes=31),
        )

    def cancel_payment(self, payment):
        if self.fail_cancel:
            raise ProviderError("cannot cancel", code="network")
        self.cancelled.append(payment.provider_payment_id)


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["mock", "stripe"],
)
class ProviderSelectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeStripe()
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
        factories.make_keys(self.product, 5, self.staff)

    def _order(self, provider="stripe"):
        from marketplace.services import cart as cart_service

        cart_service.add_item(self.buyer, self.product, 1)
        return checkout_service.create_order(self.buyer, provider_slug=provider)

    def test_choosing_a_provider_stamps_it_on_the_order_and_attempt(self):
        order, payment, intent = self._order("stripe")
        self.assertEqual(payment.provider, "stripe")
        self.assertEqual(payment.attempt, 1)
        self.assertEqual(payment.idempotency_key, f"{order.reference}:stripe:1")
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.provider, "stripe")
        self.assertEqual(payment.checkout_url, "https://pay.example.test/1")
        self.assertIsNotNone(payment.expires_at)
        self.assertEqual(intent.redirect_url, "https://pay.example.test/1")

    def test_provider_is_required_when_more_than_one_is_available(self):
        with self.assertRaises(checkout_service.CheckoutError):
            self._order(provider=None)

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(checkout_service.CheckoutError):
            self._order("paypal")
        self.assertEqual(Order.objects.count(), 0)

    def test_provider_kill_switch_hides_the_provider(self):
        MarketplaceSettings.objects.filter(pk=1).update(stripe_enabled=False)
        flags_service.invalidate()
        self.assertEqual([p.slug for p in providers.available_providers()], ["mock"])
        with self.assertRaises(checkout_service.CheckoutError):
            self._order("stripe")

    def test_purchases_kill_switch_hides_real_providers(self):
        MarketplaceSettings.objects.filter(pk=1).update(purchases_enabled=False)
        flags_service.invalidate()
        self.assertFalse(flags_service.provider_enabled("stripe"))

    def test_provider_not_in_env_allowlist_is_unavailable(self):
        with override_settings(PAYMENT_PROVIDERS_ENABLED=["mock"]):
            self.assertEqual([p.slug for p in providers.available_providers()], ["mock"])

    def test_total_below_provider_minimum_is_rejected_without_creating_an_order(self):
        cheap = factories.make_product(
            game=factories.make_game("Cheap Game"), unit_price_minor=20, slug="cheap"
        )
        factories.make_keys(cheap, 1, self.staff, prefix="CHEAP")
        from marketplace.services import cart as cart_service

        cart_service.add_item(self.buyer, cheap, 1)
        with self.assertRaises(checkout_service.CheckoutError):
            checkout_service.create_order(self.buyer, provider_slug="stripe")
        self.assertEqual(Order.objects.count(), 0)

    def test_provider_failure_keeps_order_pending_and_returns_no_intent(self):
        self.fake.fail_create = True
        order, payment, intent = self._order("stripe")
        self.assertIsNone(intent)
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(payment.failure_code, "network")
        self.assertEqual(payment.provider_payment_id, None)

    def test_retry_after_provider_failure_reuses_the_same_attempt_and_key(self):
        self.fake.fail_create = True
        order, payment, _ = self._order("stripe")
        self.fake.fail_create = False

        retried, intent = payments_service.start_or_resume_payment(order, "stripe")

        self.assertEqual(retried.pk, payment.pk)
        self.assertEqual(retried.idempotency_key, payment.idempotency_key)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        self.assertIsNotNone(intent)

    def test_resume_returns_the_existing_checkout_url_without_a_new_session(self):
        order, payment, _ = self._order("stripe")
        created_before = list(self.fake.created)

        resumed, intent = payments_service.start_or_resume_payment(order, "stripe")

        self.assertEqual(resumed.pk, payment.pk)
        self.assertEqual(intent.redirect_url, "https://pay.example.test/1")
        self.assertEqual(self.fake.created, created_before)

    def test_switching_provider_cancels_the_previous_attempt_first(self):
        order, first, _ = self._order("stripe")

        second, intent = payments_service.start_or_resume_payment(order, "mock")

        first.refresh_from_db()
        self.assertEqual(first.status, Payment.Status.CANCELLED)
        self.assertEqual(self.fake.cancelled, [first.provider_payment_id])
        self.assertEqual(second.attempt, 2)
        self.assertEqual(second.provider, "mock")
        self.assertIsNotNone(intent)

    def test_expired_session_is_replaced_by_a_new_attempt(self):
        order, first, _ = self._order("stripe")
        Payment.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        first.refresh_from_db()

        second, _ = payments_service.start_or_resume_payment(order, "stripe")

        self.assertEqual(second.attempt, 2)
        self.assertEqual(second.idempotency_key, f"{order.reference}:stripe:2")
        self.assertEqual(self.fake.cancelled, [first.provider_payment_id])

    def test_no_new_attempt_if_previous_cannot_be_cancelled(self):
        order, first, _ = self._order("stripe")
        self.fake.fail_cancel = True

        with self.assertRaises(payments_service.PaymentError):
            payments_service.start_or_resume_payment(order, "mock")

        first.refresh_from_db()
        self.assertEqual(first.status, Payment.Status.CREATED)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)

    def test_no_new_attempt_while_a_payment_is_being_processed(self):
        order, first, _ = self._order("stripe")
        Payment.objects.filter(pk=first.pk).update(status=Payment.Status.PENDING)

        with self.assertRaises(payments_service.PaymentError):
            payments_service.start_or_resume_payment(order, "mock")

    def test_cannot_pay_an_expired_reservation(self):
        order, _, _ = self._order("stripe")
        Order.objects.filter(pk=order.pk).update(
            reservation_expires_at=timezone.now() - timedelta(minutes=1)
        )
        with self.assertRaises(payments_service.PaymentError):
            payments_service.start_or_resume_payment(order, "stripe")

    def test_cannot_pay_a_non_pending_order(self):
        order, _, _ = self._order("stripe")
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID)
        with self.assertRaises(payments_service.PaymentError):
            payments_service.start_or_resume_payment(order, "stripe")


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["mock", "stripe"],
)
class ProviderSelectionViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeStripe()
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
        factories.make_keys(self.product, 5, self.staff)
        self.client.login(username="buyer", password="testpass123")
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})

    def test_checkout_page_lists_available_providers(self):
        response = self.client.get(reverse("marketplace:checkout"))
        self.assertContains(response, 'value="stripe"')
        self.assertContains(response, 'value="mock"')

    def test_confirm_without_a_choice_is_rejected(self):
        response = self.client.post(reverse("marketplace:checkout_confirm"))
        self.assertRedirects(response, reverse("marketplace:checkout"))
        self.assertEqual(Order.objects.count(), 0)

    def test_confirm_redirects_to_the_provider_hosted_page(self):
        response = self.client.post(reverse("marketplace:checkout_confirm"), {"provider": "stripe"})
        self.assertRedirects(response, "https://pay.example.test/1", fetch_redirect_response=False)

    def test_provider_outage_redirects_to_the_order_with_a_retry_option(self):
        self.fake.fail_create = True
        response = self.client.post(reverse("marketplace:checkout_confirm"), {"provider": "stripe"})
        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(response, reverse("marketplace:order_detail", args=[order.reference]))
        page = self.client.get(reverse("marketplace:order_detail", args=[order.reference]))
        self.assertContains(page, "Pay now")

    def test_order_pay_is_post_only_and_owner_scoped(self):
        self.client.post(reverse("marketplace:checkout_confirm"), {"provider": "stripe"})
        order = Order.objects.get(user=self.buyer)
        url = reverse("marketplace:order_pay", args=[order.reference])
        self.assertEqual(self.client.get(url).status_code, 405)

        factories.make_user("intruder")
        self.client.logout()
        self.client.login(username="intruder", password="testpass123")
        self.assertEqual(self.client.post(url, {"provider": "stripe"}).status_code, 404)

    def test_order_pay_redirects_to_the_resumed_session(self):
        self.client.post(reverse("marketplace:checkout_confirm"), {"provider": "stripe"})
        order = Order.objects.get(user=self.buyer)
        response = self.client.post(
            reverse("marketplace:order_pay", args=[order.reference]), {"provider": "stripe"}
        )
        self.assertRedirects(response, "https://pay.example.test/1", fetch_redirect_response=False)
