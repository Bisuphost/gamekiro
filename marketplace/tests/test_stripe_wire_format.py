import json
from urllib.parse import parse_qsl

import stripe
from django.test import TestCase, override_settings

from marketplace.models import Order, Payment
from marketplace.providers.base import ProviderError
from marketplace.providers.stripe_provider import StripeProvider
from marketplace.services import payments as payments_service

from . import factories


class RecordingHttpClient(stripe.HTTPClient):
    name = "recording"

    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, headers, post_data=None, *, _usage=None):
        self.requests.append((method, url, dict(headers or {}), post_data))
        status, body = self.responses.pop(0)
        return json.dumps(body), status, {}

    def request_stream(self, *args, **kwargs):
        raise NotImplementedError

    def close(self):
        pass


SESSION_RESPONSE = {
    "id": "cs_test_wire",
    "object": "checkout.session",
    "url": "https://checkout.stripe.com/c/pay/cs_test_wire",
    "status": "open",
    "payment_status": "unpaid",
}


@override_settings(STRIPE_SECRET_KEY="sk_test_wire", STRIPE_WEBHOOK_SECRETS=["whsec_x"])
class StripeWireFormatTests(TestCase):
    def setUp(self):
        buyer = factories.make_user("buyer", email="buyer@example.com")
        self.order = Order.objects.create(
            user=buyer, currency="USD", total_minor=1999, subtotal_minor=1999
        )
        self.payment = Payment.objects.create(
            order=self.order,
            provider="stripe",
            amount_minor=1999,
            currency="USD",
            idempotency_key=f"{self.order.reference}:stripe:1",
        )

    def _provider(self, responses):
        http = RecordingHttpClient(responses)
        client = stripe.StripeClient("sk_test_wire", http_client=http, max_network_retries=0)
        return StripeProvider(client=client), http

    def test_create_session_is_form_encoded_with_expected_fields_and_headers(self):
        provider, http = self._provider([(200, SESSION_RESPONSE)])

        intent = provider.create_payment(
            self.order, self.payment, payments_service.payment_urls(self.order)
        )

        method, url, headers, body = http.requests[0]
        self.assertEqual(method, "post")
        self.assertTrue(url.endswith("/v1/checkout/sessions"))
        self.assertEqual(headers["Idempotency-Key"], f"{self.payment.idempotency_key}:create")
        self.assertTrue(headers["Authorization"].startswith("Bearer sk_test_"))
        fields = dict(parse_qsl(body))
        self.assertEqual(fields["mode"], "payment")
        self.assertEqual(fields["client_reference_id"], self.order.reference)
        self.assertEqual(fields["line_items[0][quantity]"], "1")
        self.assertEqual(fields["line_items[0][price_data][unit_amount]"], "1999")
        self.assertEqual(fields["line_items[0][price_data][currency]"], "usd")
        self.assertEqual(fields["payment_method_types[0]"], "card")
        self.assertEqual(fields["metadata[order_reference]"], self.order.reference)
        self.assertEqual(
            fields["payment_intent_data[metadata][order_reference]"], self.order.reference
        )
        self.assertEqual(fields["customer_email"], "buyer@example.com")
        self.assertIn("{CHECKOUT_SESSION_ID}", fields["success_url"])
        self.assertTrue(fields["expires_at"].isdigit())
        self.assertNotIn("automatic_tax[enabled]", fields)
        self.assertEqual(intent.provider_payment_id, "cs_test_wire")
        self.assertEqual(intent.redirect_url, SESSION_RESPONSE["url"])

    def test_stripe_400_becomes_a_definitive_provider_error(self):
        error = {
            "error": {"type": "invalid_request_error", "message": "bad", "param": "expires_at"}
        }
        provider, _ = self._provider([(400, error)])
        with self.assertRaises(ProviderError) as ctx:
            provider.create_payment(
                self.order, self.payment, payments_service.payment_urls(self.order)
            )
        self.assertTrue(ctx.exception.definitive)

    def test_stripe_500_becomes_an_indefinite_provider_error(self):
        error = {"error": {"type": "api_error", "message": "oops"}}
        provider, _ = self._provider([(500, error)])
        with self.assertRaises(ProviderError) as ctx:
            provider.create_payment(
                self.order, self.payment, payments_service.payment_urls(self.order)
            )
        self.assertFalse(ctx.exception.definitive)

    def test_stripe_429_becomes_an_indefinite_provider_error(self):
        error = {"error": {"type": "invalid_request_error", "message": "slow down"}}
        provider, _ = self._provider([(429, error)])
        with self.assertRaises(ProviderError) as ctx:
            provider.create_payment(
                self.order, self.payment, payments_service.payment_urls(self.order)
            )
        self.assertFalse(ctx.exception.definitive)

    def test_refund_is_form_encoded_against_the_payment_intent(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_reference="pi_wire")
        self.payment.refresh_from_db()
        provider, http = self._provider(
            [(200, {"id": "re_wire", "object": "refund", "status": "pending"})]
        )
        result = provider.refund(self.payment, 500, "refund-42")
        method, url, headers, body = http.requests[0]
        fields = dict(parse_qsl(body))
        self.assertTrue(url.endswith("/v1/refunds"))
        self.assertEqual(fields["payment_intent"], "pi_wire")
        self.assertEqual(fields["amount"], "500")
        self.assertEqual(headers["Idempotency-Key"], "refund-42")
        self.assertEqual((result.provider_refund_id, result.status), ("re_wire", "pending"))

    def test_retrieve_of_a_paid_session_yields_a_success_event_from_real_sdk_objects(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id="cs_test_wire")
        self.payment.refresh_from_db()
        paid = {
            "id": "cs_test_wire",
            "object": "checkout.session",
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "client_reference_id": self.order.reference,
            "amount_total": 1999,
            "currency": "usd",
            "payment_intent": "pi_wire",
            "metadata": {"order_reference": self.order.reference},
        }
        provider, http = self._provider([(200, paid)])

        state = provider.fetch_payment_state(self.payment)

        self.assertEqual(http.requests[0][0], "get")
        self.assertTrue(http.requests[0][1].endswith("/v1/checkout/sessions/cs_test_wire"))
        self.assertEqual(state.status, "paid")
        self.assertEqual(state.event.event_type, "payment_succeeded")
        self.assertEqual(state.event.order_reference, self.order.reference)
        self.assertEqual(state.event.provider_payment_id, "cs_test_wire")
        self.assertEqual(state.event.provider_reference, "pi_wire")
        self.assertEqual(state.event.amount_minor, 1999)
        self.assertEqual(state.event.currency, "USD")

    def test_retrieve_of_an_open_session_yields_no_event(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id="cs_test_wire")
        self.payment.refresh_from_db()
        provider, _ = self._provider([(200, SESSION_RESPONSE)])
        state = provider.fetch_payment_state(self.payment)
        self.assertEqual(state.status, "open")
        self.assertIsNone(state.event)

    def test_expire_posts_to_the_expire_endpoint(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id="cs_test_wire")
        self.payment.refresh_from_db()
        provider, http = self._provider([(200, {**SESSION_RESPONSE, "status": "expired"})])
        provider.cancel_payment(self.payment)
        method, url, _, _ = http.requests[0]
        self.assertEqual(method, "post")
        self.assertTrue(url.endswith("/v1/checkout/sessions/cs_test_wire/expire"))
