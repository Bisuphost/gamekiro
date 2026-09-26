import json
from datetime import timedelta
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketplace import providers
from marketplace.models import (
    Dispute,
    Entitlement,
    GameKey,
    MarketplaceSettings,
    Order,
    OutboundEmail,
    Payment,
    Refund,
    WebhookEvent,
)
from marketplace.providers import paypal_provider
from marketplace.providers.base import (
    ProviderError,
    WebhookVerificationError,
    WebhookVerificationUnavailable,
)
from marketplace.providers.paypal_provider import (
    PayPalProvider,
    amount_string_to_minor,
    minor_to_amount_string,
)
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import flags as flags_service
from marketplace.services import payments as payments_service
from marketplace.services import refunds as refunds_service

from . import factories

PAYPAL_SETTINGS = dict(
    PAYPAL_ENV="sandbox",
    PAYPAL_CLIENT_ID="client-id",
    PAYPAL_CLIENT_SECRET="client-secret",
    PAYPAL_WEBHOOK_ID="WH-CONFIGURED",
)

WEBHOOK_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "69cd13f0-d67a-11e5-baa3-778b53f4ae55",
    "PAYPAL-TRANSMISSION-SIG": "c2lnbmF0dXJl",
    "PAYPAL-TRANSMISSION-TIME": "2026-09-21T20:01:35Z",
}


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def capture_object(capture_id="CAP1", status="COMPLETED", value="19.99", currency="USD"):
    return {
        "id": capture_id,
        "status": status,
        "amount": {"currency_code": currency, "value": value},
        "final_capture": True,
    }


class FakePayPal:
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.tokens_issued = 0
        self.capture_mode = "complete"
        self.capture_value = None
        self.verify_result = "SUCCESS"
        self.verify_network_error = False
        self.network_errors_left = 0
        self.expire_next_token = False
        self.refunds = {}
        self.refunds_issued = 0
        self.refund_status = "COMPLETED"

    def calls_to(self, method, suffix):
        return [c for c in self.calls if c["method"] == method and c["url"].endswith(suffix)]

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        path = url.split("paypal.com", 1)[1]

        if path == "/v1/oauth2/token":
            self.tokens_issued += 1
            return FakeResponse(
                200, {"access_token": f"token-{self.tokens_issued}", "expires_in": 32400}
            )
        if self.expire_next_token and kwargs["headers"]["Authorization"] == "Bearer token-1":
            self.expire_next_token = False
            return FakeResponse(401, {"name": "AUTHENTICATION_FAILURE"})
        if path == "/v1/notifications/verify-webhook-signature":
            if self.verify_network_error:
                raise requests.ConnectionError("down")
            return FakeResponse(200, {"verification_status": self.verify_result})
        if path == "/v2/checkout/orders" and method == "POST":
            if self.network_errors_left:
                self.network_errors_left -= 1
                raise requests.ConnectionError("down")
            body = json.loads(kwargs["data"])
            order_id = f"PPORDER{len(self.orders) + 1}"
            unit = body["purchase_units"][0]
            self.orders[order_id] = {
                "id": order_id,
                "status": "PAYER_ACTION_REQUIRED",
                "purchase_units": [
                    {"custom_id": unit["custom_id"], "amount": unit["amount"], "payments": {}}
                ],
            }
            return FakeResponse(
                201,
                {
                    "id": order_id,
                    "status": "PAYER_ACTION_REQUIRED",
                    "links": [
                        {"rel": "self", "href": f"https://api-m.sandbox.paypal.com/v2/{order_id}"},
                        {
                            "rel": "payer-action",
                            "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                        },
                    ],
                },
            )
        if "/v2/checkout/orders/" in path and path.endswith("/capture"):
            order_id = path.split("/")[-2]
            return self._capture(order_id)
        if "/v2/checkout/orders/" in path and method == "GET":
            return FakeResponse(200, self.orders[path.rsplit("/", 1)[1]])
        if path.endswith("/refund"):
            self.refunds_issued += 1
            refund_id = f"REFUND{self.refunds_issued}"
            refund = {"id": refund_id, "status": self.refund_status}
            self.refunds[refund_id] = refund
            return FakeResponse(201, refund)
        if "/v2/payments/refunds/" in path and method == "GET":
            return FakeResponse(200, self.refunds[path.rsplit("/", 1)[1]])
        raise AssertionError(f"Unexpected PayPal call {method} {url}")

    def approve(self, order_id):
        self.orders[order_id]["status"] = "APPROVED"

    def _capture(self, order_id):
        order = self.orders[order_id]
        unit = order["purchase_units"][0]
        value = self.capture_value or unit["amount"]["value"]
        mode = self.capture_mode
        if mode == "network":
            raise requests.ReadTimeout("slow")
        if mode == "payer_action":
            return FakeResponse(
                422,
                {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "PAYER_ACTION_REQUIRED"}]},
            )
        if mode == "in_progress":
            return FakeResponse(
                422,
                {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "PREVIOUS_REQUEST_IN_PROGRESS"}],
                },
            )
        if mode == "already_captured":
            order["status"] = "COMPLETED"
            unit["payments"] = {"captures": [capture_object("CAPX", value=value)]}
            return FakeResponse(
                422,
                {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_ALREADY_CAPTURED"}]},
            )
        status = {"complete": "COMPLETED", "pending": "PENDING", "declined": "DECLINED"}[mode]
        order["status"] = "COMPLETED"
        unit["payments"] = {"captures": [capture_object("CAP1", status=status, value=value)]}
        return FakeResponse(201, order)


def make_provider():
    fake = FakePayPal()
    return PayPalProvider(session=fake), fake


class AmountConversionTests(SimpleTestCase):
    def test_minor_units_become_exact_decimal_strings(self):
        self.assertEqual(minor_to_amount_string(1999, "USD"), "19.99")
        self.assertEqual(minor_to_amount_string(5, "USD"), "0.05")
        self.assertEqual(minor_to_amount_string(100, "usd"), "1.00")
        self.assertEqual(minor_to_amount_string(123456789, "USD"), "1234567.89")

    def test_unsupported_currency_is_refused(self):
        with self.assertRaises(ProviderError):
            minor_to_amount_string(1000, "JPY")

    def test_amount_strings_parse_to_exact_minor_units(self):
        self.assertEqual(amount_string_to_minor("19.99", "USD"), 1999)
        self.assertEqual(amount_string_to_minor("0.10", "USD"), 10)
        self.assertEqual(amount_string_to_minor("20", "USD"), 2000)
        self.assertEqual(amount_string_to_minor("1.1", "USD"), 110)

    def test_malformed_or_ambiguous_amounts_are_refused(self):
        for bad in ("19.999", "-1.00", "abc", "", "1e2", "NaN", "Infinity", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                amount_string_to_minor(bad, "USD")

    def test_no_float_rounding_error(self):
        self.assertEqual(amount_string_to_minor("0.29", "USD"), 29)
        self.assertEqual(amount_string_to_minor("4.35", "USD"), 435)
        self.assertEqual(amount_string_to_minor("1.15", "USD"), 115)


@override_settings(**PAYPAL_SETTINGS)
class PayPalWebhookVerificationTests(SimpleTestCase):
    def setUp(self):
        paypal_provider._token_cache.clear()
        self.provider, self.fake = make_provider()
        self.body = json.dumps(
            {
                "id": "WH-EVT-1",
                "event_type": "PAYMENT.CAPTURE.COMPLETED",
                "resource": {
                    "id": "CAP1",
                    "status": "COMPLETED",
                    "amount": {"currency_code": "USD", "value": "19.99"},
                    "custom_id": "ORD-ABC",
                    "supplementary_data": {"related_ids": {"order_id": "PPORDER1"}},
                },
            }
        ).encode()

    def test_verified_capture_completed_is_normalized(self):
        event = self.provider.verify_webhook(self.body, WEBHOOK_HEADERS)
        self.assertEqual(event.event_id, "WH-EVT-1")
        self.assertEqual(event.event_type, "payment_succeeded")
        self.assertEqual(event.order_reference, "ORD-ABC")
        self.assertEqual(event.provider_payment_id, "PPORDER1")
        self.assertEqual(event.provider_reference, "CAP1")
        self.assertEqual(event.amount_minor, 1999)
        self.assertEqual(event.currency, "USD")
        self.assertEqual(event.provider, "paypal")

    def test_verification_request_carries_the_body_byte_for_byte(self):
        odd = (
            b'{ "id" : "WH-EVT-2",\n  "event_type":"CUSTOMER.DISPUTE.CREATED",'
            b'"resource":{"note":"caf\\u00e9  spaced"} }'
        )
        self.provider.verify_webhook(odd, WEBHOOK_HEADERS)
        posted = self.fake.calls_to("POST", "/v1/notifications/verify-webhook-signature")[0]["data"]
        self.assertIn(b'"webhook_event":' + odd.strip() + b"}", posted)
        parsed = json.loads(posted)
        self.assertEqual(parsed["webhook_id"], "WH-CONFIGURED")
        self.assertEqual(parsed["transmission_id"], WEBHOOK_HEADERS["PAYPAL-TRANSMISSION-ID"])
        self.assertEqual(parsed["cert_url"], WEBHOOK_HEADERS["PAYPAL-CERT-URL"])

    def test_failure_status_is_rejected(self):
        self.fake.verify_result = "FAILURE"
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(self.body, WEBHOOK_HEADERS)

    def test_each_missing_header_is_rejected(self):
        for name in WEBHOOK_HEADERS:
            headers = {k: v for k, v in WEBHOOK_HEADERS.items() if k != name}
            with self.subTest(missing=name), self.assertRaises(WebhookVerificationError):
                self.provider.verify_webhook(self.body, headers)
        self.assertEqual(
            self.fake.calls_to("POST", "/v1/notifications/verify-webhook-signature"), []
        )

    @override_settings(PAYPAL_WEBHOOK_ID=None)
    def test_missing_webhook_id_rejects_everything(self):
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(self.body, WEBHOOK_HEADERS)

    def test_unreachable_verification_is_reported_as_unavailable_not_invalid(self):
        self.fake.verify_network_error = True
        with self.assertRaises(WebhookVerificationUnavailable):
            self.provider.verify_webhook(self.body, WEBHOOK_HEADERS)

    def test_json_injection_through_the_body_is_rejected_before_any_call(self):
        evil = b'{"id":"x","event_type":"y"}, "webhook_id":"attacker"'
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(evil, WEBHOOK_HEADERS)
        self.assertEqual(
            self.fake.calls_to("POST", "/v1/notifications/verify-webhook-signature"), []
        )

    def test_non_object_and_non_utf8_bodies_are_rejected(self):
        for bad in (b"[]", b'"string"', b"\xff\xfe", b"{}", b'{"id":"a"}'):
            with self.subTest(bad=bad), self.assertRaises(WebhookVerificationError):
                self.provider.verify_webhook(bad, WEBHOOK_HEADERS)

    def _normalize(self, event_type, resource, event_id="WH-N"):
        body = json.dumps({"id": event_id, "event_type": event_type, "resource": resource}).encode()
        return self.provider.verify_webhook(body, WEBHOOK_HEADERS)

    def test_order_approved_maps_to_buyer_approved(self):
        event = self._normalize(
            "CHECKOUT.ORDER.APPROVED",
            {"id": "PPORDER9", "purchase_units": [{"custom_id": "ORD-XYZ"}]},
        )
        self.assertEqual(event.event_type, "buyer_approved")
        self.assertEqual(event.provider_payment_id, "PPORDER9")
        self.assertEqual(event.order_reference, "ORD-XYZ")

    def test_declined_capture_ends_only_the_attempt(self):
        event = self._normalize(
            "PAYMENT.CAPTURE.DECLINED",
            {
                "id": "CAP2",
                "status": "DECLINED",
                "amount": {"currency_code": "USD", "value": "1.00"},
            },
        )
        self.assertEqual(event.event_type, "attempt_failed")

    def test_pending_capture_is_not_treated_as_paid(self):
        event = self._normalize(
            "PAYMENT.CAPTURE.PENDING",
            {
                "id": "CAP2",
                "status": "PENDING",
                "amount": {"currency_code": "USD", "value": "1.00"},
            },
        )
        self.assertEqual(event.event_type, "payment_pending")

    def test_completed_event_with_non_completed_resource_is_not_success(self):
        event = self._normalize(
            "PAYMENT.CAPTURE.COMPLETED",
            {
                "id": "CAP2",
                "status": "PENDING",
                "amount": {"currency_code": "USD", "value": "1.00"},
            },
        )
        self.assertEqual(event.event_type, "payment_pending")

    def test_capture_with_an_unparseable_amount_is_rejected(self):
        with self.assertRaises(WebhookVerificationError):
            self._normalize(
                "PAYMENT.CAPTURE.COMPLETED",
                {
                    "id": "C",
                    "status": "COMPLETED",
                    "amount": {"currency_code": "USD", "value": "1.999"},
                },
            )

    def test_unrecognized_event_types_pass_through_unchanged(self):
        event = self._normalize("SOME.OTHER.EVENT", {"id": "X-1"})
        self.assertEqual(event.event_type, "SOME.OTHER.EVENT")

    def test_inquiry_stage_dispute_created_is_tracked_but_not_escalated(self):
        event = self._normalize(
            "CUSTOMER.DISPUTE.CREATED",
            {"dispute_id": "PP-D-1", "dispute_life_cycle_stage": "INQUIRY", "status": "OPEN"},
        )
        self.assertEqual(event.event_type, "dispute_updated")
        self.assertEqual(event.detail["dispute_id"], "PP-D-1")
        self.assertEqual(event.detail["stage"], "INQUIRY")

    def test_chargeback_stage_dispute_created_is_escalated(self):
        event = self._normalize(
            "CUSTOMER.DISPUTE.CREATED",
            {
                "dispute_id": "PP-D-2",
                "dispute_life_cycle_stage": "CHARGEBACK",
                "status": "OPEN",
                "reason": "UNAUTHORIZED",
                "dispute_amount": {"currency_code": "USD", "value": "19.99"},
                "seller_response_due_date": "2026-10-05T00:00:00Z",
            },
        )
        self.assertEqual(event.event_type, "dispute_created")
        self.assertEqual(event.amount_minor, 1999)
        self.assertEqual(event.currency, "USD")
        self.assertEqual(event.detail["due_by"], "2026-10-05T00:00:00Z")

    def test_resolved_dispute_is_closed_with_its_outcome(self):
        event = self._normalize(
            "CUSTOMER.DISPUTE.RESOLVED",
            {
                "dispute_id": "PP-D-3",
                "dispute_life_cycle_stage": "CHARGEBACK",
                "status": "RESOLVED",
                "dispute_outcome": {"outcome_code": "RESOLVED_SELLER_FAVOUR"},
            },
        )
        self.assertEqual(event.event_type, "dispute_closed")
        self.assertEqual(event.detail["outcome"], "RESOLVED_SELLER_FAVOUR")

    def test_stored_payload_holds_no_payer_pii(self):
        body = json.dumps(
            {
                "id": "WH-PII",
                "event_type": "PAYMENT.CAPTURE.COMPLETED",
                "resource": {
                    "id": "CAP1",
                    "status": "COMPLETED",
                    "amount": {"currency_code": "USD", "value": "1.00"},
                    "payer": {"email_address": "payer@example.com", "name": {"given_name": "Pat"}},
                },
            }
        ).encode()
        event = self.provider.verify_webhook(body, WEBHOOK_HEADERS)
        stored = json.dumps(event.raw)
        self.assertNotIn("payer@example.com", stored)
        self.assertNotIn("Pat", stored)
        self.assertEqual(self.provider.normalize_event(event.raw), event)


@override_settings(**PAYPAL_SETTINGS)
class PayPalHttpClientTests(TestCase):
    def setUp(self):
        paypal_provider._token_cache.clear()
        self.provider, self.fake = make_provider()
        buyer = factories.make_user("buyer")
        self.order = Order.objects.create(
            user=buyer,
            currency="USD",
            total_minor=1999,
            subtotal_minor=1999,
            reservation_expires_at=timezone.now() + timedelta(minutes=35),
        )
        self.payment = Payment.objects.create(
            order=self.order,
            provider="paypal",
            amount_minor=1999,
            currency="USD",
            idempotency_key=f"{self.order.reference}:paypal:1",
        )
        self.urls = payments_service.payment_urls(self.order)

    def test_create_order_body_and_headers_follow_the_reviewed_design(self):
        intent = self.provider.create_payment(self.order, self.payment, self.urls)

        call = self.fake.calls_to("POST", "/v2/checkout/orders")[0]
        body = json.loads(call["data"])
        unit = body["purchase_units"][0]
        context = body["payment_source"]["paypal"]["experience_context"]
        self.assertEqual(body["intent"], "CAPTURE")
        self.assertEqual(unit["amount"], {"currency_code": "USD", "value": "19.99"})
        self.assertEqual(unit["custom_id"], self.order.reference)
        self.assertEqual(context["shipping_preference"], "NO_SHIPPING")
        self.assertEqual(context["user_action"], "PAY_NOW")
        self.assertEqual(context["return_url"], self.urls.success_url)
        self.assertEqual(context["cancel_url"], self.urls.cancel_url)
        self.assertEqual(
            call["headers"]["PayPal-Request-Id"], f"create:{self.payment.idempotency_key}"
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer token-1")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(intent.provider_payment_id, "PPORDER1")
        self.assertEqual(
            intent.redirect_url, "https://www.sandbox.paypal.com/checkoutnow?token=PPORDER1"
        )

    def test_token_is_requested_with_client_credentials_and_cached(self):
        self.provider.create_payment(self.order, self.payment, self.urls)
        self.provider.create_payment(self.order, self.payment, self.urls)
        token_calls = self.fake.calls_to("POST", "/v1/oauth2/token")
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(token_calls[0]["auth"], ("client-id", "client-secret"))
        self.assertEqual(token_calls[0]["data"], {"grant_type": "client_credentials"})

    def test_rejected_token_is_refreshed_once_and_the_call_retried(self):
        self.fake.expire_next_token = True
        self.provider.create_payment(self.order, self.payment, self.urls)
        self.assertEqual(self.fake.tokens_issued, 2)
        orders_calls = self.fake.calls_to("POST", "/v2/checkout/orders")
        self.assertEqual(orders_calls[-1]["headers"]["Authorization"], "Bearer token-2")

    def test_transient_network_error_is_retried_with_the_same_request_id(self):
        self.fake.network_errors_left = 1
        self.provider.create_payment(self.order, self.payment, self.urls)
        first, second = self.fake.calls_to("POST", "/v2/checkout/orders")
        self.assertEqual(
            first["headers"]["PayPal-Request-Id"], second["headers"]["PayPal-Request-Id"]
        )

    def test_persistent_network_error_is_not_definitive(self):
        self.fake.network_errors_left = 5
        with self.assertRaises(ProviderError) as ctx:
            self.provider.create_payment(self.order, self.payment, self.urls)
        self.assertFalse(ctx.exception.definitive)

    def test_no_redirects_are_followed_and_timeouts_are_set(self):
        self.provider.create_payment(self.order, self.payment, self.urls)
        for call in self.fake.calls:
            self.assertIs(call["allow_redirects"], False)
            self.assertEqual(call["timeout"], (3.05, 10))

    def test_unsupported_currency_never_reaches_paypal(self):
        self.order.currency = "JPY"
        with self.assertRaises(ProviderError):
            self.provider.create_payment(self.order, self.payment, self.urls)
        self.assertEqual(self.fake.calls_to("POST", "/v2/checkout/orders"), [])

    def test_refund_targets_the_capture_with_an_exact_amount_and_caller_key(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_reference="CAP77")
        self.payment.refresh_from_db()
        result = self.provider.refund(self.payment, 250, "refund-9")
        call = self.fake.calls_to("POST", "/v2/payments/captures/CAP77/refund")[0]
        self.assertEqual(
            json.loads(call["data"])["amount"], {"currency_code": "USD", "value": "2.50"}
        )
        self.assertEqual(call["headers"]["PayPal-Request-Id"], "refund-9")
        self.assertEqual((result.provider_refund_id, result.status), ("REFUND1", "COMPLETED"))

    def test_refund_without_a_capture_is_refused(self):
        with self.assertRaises(ProviderError):
            self.provider.refund(self.payment, 250, "refund-9")


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["paypal"],
    SITE_URL="https://shop.example.test",
    **PAYPAL_SETTINGS,
)
class PayPalEndToEndTests(TestCase):
    def setUp(self):
        cache.clear()
        paypal_provider._token_cache.clear()
        self.provider, self.fake = make_provider()
        patcher = mock.patch.dict(providers.PROVIDERS, {"paypal": self.provider})
        patcher.start()
        self.addCleanup(patcher.stop)
        MarketplaceSettings.objects.update_or_create(
            pk=1,
            defaults={
                "marketplace_enabled": True,
                "purchases_enabled": True,
                "fulfillment_enabled": True,
                "paypal_enabled": True,
            },
        )
        flags_service.invalidate()
        self.staff = factories.make_user("staff")
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product(unit_price_minor=1999)
        factories.make_keys(self.product, 5, self.staff)
        cart_service.add_item(self.buyer, self.product, 1)
        self.order, self.payment, self.intent = checkout_service.create_order(
            self.buyer, provider_slug="paypal"
        )
        self.paypal_order_id = self.intent.provider_payment_id
        self.client.login(username="buyer", password="testpass123")

    def _return(self, token=None, client=None):
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.client).get(
                reverse("marketplace:order_detail", args=[self.order.reference]),
                {"token": token or self.paypal_order_id, "PayerID": "PAYER1"},
            )

    def _post_webhook(self, event_type, resource, event_id="WH-1", headers=None):
        body = json.dumps({"id": event_id, "event_type": event_type, "resource": resource})
        extra = {
            "HTTP_" + k.replace("-", "_"): v
            for k, v in (WEBHOOK_HEADERS if headers is None else headers).items()
        }
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("marketplace:webhook", args=["paypal"]),
                data=body,
                content_type="application/json",
                **extra,
            )

    def _refresh(self):
        self.order.refresh_from_db()
        self.payment.refresh_from_db()

    def test_checkout_redirects_to_the_paypal_approval_page(self):
        self._refresh()
        self.assertEqual(self.order.provider, "paypal")
        self.assertEqual(self.payment.provider_payment_id, self.paypal_order_id)
        self.assertEqual(self.payment.status, Payment.Status.CREATED)
        self.assertIn("checkoutnow?token=", self.intent.redirect_url)

    def test_return_after_approval_captures_once_and_fulfils(self):
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.provider_reference, "CAP1")
        self.assertEqual(len(self.fake.calls_to("POST", f"/{self.paypal_order_id}/capture")), 1)
        capture = self.fake.calls_to("POST", f"/{self.paypal_order_id}/capture")[0]
        self.assertEqual(
            capture["headers"]["PayPal-Request-Id"], f"capture:{self.payment.idempotency_key}"
        )
        self.assertEqual(capture["headers"]["Prefer"], "return=representation")

    def test_return_before_approval_does_not_capture(self):
        self._return()
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])

    def test_return_with_a_foreign_token_does_not_capture(self):
        self.fake.approve(self.paypal_order_id)
        self._return(token="SOMEONEELSESORDER")
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])

    def test_return_by_another_user_is_404_and_does_not_capture(self):
        self.fake.approve(self.paypal_order_id)
        factories.make_user("intruder")
        other = self.client_class()
        other.login(username="intruder", password="testpass123")
        response = self._return(client=other)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])

    def test_repeated_return_does_not_capture_twice(self):
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._return()
        self.assertEqual(len(self.fake.calls_to("POST", "/capture")), 1)

    def test_expired_reservation_is_never_captured(self):
        self.fake.approve(self.paypal_order_id)
        Order.objects.filter(pk=self.order.pk).update(
            reservation_expires_at=timezone.now() - timedelta(minutes=1)
        )
        self._return()
        self._refresh()
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.payment.status, Payment.Status.CREATED)

    def test_order_no_longer_pending_is_never_captured(self):
        self.fake.approve(self.paypal_order_id)
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.EXPIRED)
        payments_service.reconcile_order(Order.objects.get(pk=self.order.pk))
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])

    def test_a_superseded_attempt_is_never_captured(self):
        Payment.objects.filter(pk=self.payment.pk).update(status=Payment.Status.CANCELLED)
        self.fake.approve(self.paypal_order_id)
        self._return()
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])

    def test_pending_capture_waits_for_the_webhook_and_never_fulfils_early(self):
        self.fake.capture_mode = "pending"
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.payment.status, Payment.Status.PENDING)

        response = self._post_webhook(
            "PAYMENT.CAPTURE.COMPLETED",
            {
                "id": "CAP1",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "19.99"},
                "custom_id": self.order.reference,
                "supplementary_data": {"related_ids": {"order_id": self.paypal_order_id}},
            },
        )
        self.assertEqual(response.status_code, 200)
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(self.payment.status, Payment.Status.PAID)

    def test_declined_capture_fails_the_attempt_but_keeps_the_order_payable(self):
        self.fake.capture_mode = "declined"
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._refresh()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

        second, intent = payments_service.start_or_resume_payment(self.order, "paypal")
        self.assertEqual(second.attempt, 2)
        self.assertNotEqual(intent.provider_payment_id, self.paypal_order_id)

    def test_payer_action_required_fails_the_attempt(self):
        self.fake.capture_mode = "payer_action"
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._refresh()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_already_captured_error_is_reconciled_to_success_from_the_order_state(self):
        self.fake.capture_mode = "already_captured"
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(self.payment.provider_reference, "CAPX")

    def test_in_progress_error_leaves_the_attempt_pending_for_later_reconciliation(self):
        self.fake.capture_mode = "in_progress"
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._refresh()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_capture_timeout_never_marks_failed_and_retry_reuses_the_request_id(self):
        self.fake.capture_mode = "network"
        self.fake.approve(self.paypal_order_id)
        response = self._return()
        self.assertEqual(response.status_code, 200)
        self._refresh()
        self.assertEqual(self.payment.status, Payment.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

        self._return()
        self.assertEqual(len(self.fake.calls_to("POST", "/capture")), 2)

        Payment.objects.filter(pk=self.payment.pk).update(
            updated_at=timezone.now() - timedelta(minutes=5)
        )
        self.fake.capture_mode = "complete"
        self._return()
        self._refresh()
        ids = {c["headers"]["PayPal-Request-Id"] for c in self.fake.calls_to("POST", "/capture")}
        self.assertEqual(ids, {f"capture:{self.payment.idempotency_key}"})
        self.assertEqual(self.order.status, Order.Status.FULFILLED)

    def test_fresh_pending_claim_is_not_recaptured(self):
        self.fake.capture_mode = "network"
        self.fake.approve(self.paypal_order_id)
        self._return()
        calls_after_first = len(self.fake.calls_to("POST", "/capture"))
        self.fake.capture_mode = "complete"
        self._return()
        self.assertEqual(len(self.fake.calls_to("POST", "/capture")), calls_after_first)

    def test_capture_of_the_wrong_amount_sends_the_order_to_review(self):
        self.fake.capture_value = "0.01"
        self.fake.approve(self.paypal_order_id)
        self._return()
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.NEEDS_REVIEW)
        self.assertEqual(Entitlement.objects.count(), 0)

    def test_buyer_approved_webhook_captures_when_the_buyer_closed_the_tab(self):
        self.fake.approve(self.paypal_order_id)
        response = self._post_webhook(
            "CHECKOUT.ORDER.APPROVED",
            {"id": self.paypal_order_id, "purchase_units": [{"custom_id": self.order.reference}]},
        )
        self.assertEqual(response.status_code, 200)
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(len(self.fake.calls_to("POST", "/capture")), 1)

    def test_approved_webhook_and_browser_return_capture_only_once(self):
        self.fake.approve(self.paypal_order_id)
        self._post_webhook(
            "CHECKOUT.ORDER.APPROVED",
            {"id": self.paypal_order_id, "purchase_units": [{"custom_id": self.order.reference}]},
        )
        self._return()
        self.assertEqual(len(self.fake.calls_to("POST", "/capture")), 1)
        self.assertEqual(Entitlement.objects.filter(user=self.buyer).count(), 1)

    def test_approved_webhook_for_an_expired_reservation_does_not_capture(self):
        self.fake.approve(self.paypal_order_id)
        Order.objects.filter(pk=self.order.pk).update(
            reservation_expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = self._post_webhook(
            "CHECKOUT.ORDER.APPROVED",
            {"id": self.paypal_order_id, "purchase_units": [{"custom_id": self.order.reference}]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])

    def test_approved_webhook_for_an_order_we_never_created_is_ignored(self):
        response = self._post_webhook(
            "CHECKOUT.ORDER.APPROVED",
            {"id": "FOREIGN", "purchase_units": [{"custom_id": self.order.reference}]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])

    def test_capture_webhook_without_custom_id_is_bound_through_the_order_id(self):
        response = self._post_webhook(
            "PAYMENT.CAPTURE.COMPLETED",
            {
                "id": "CAP1",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "19.99"},
                "supplementary_data": {"related_ids": {"order_id": self.paypal_order_id}},
            },
        )
        self.assertEqual(response.status_code, 200)
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)

    def test_capture_webhook_for_a_foreign_order_id_is_not_applied(self):
        self._post_webhook(
            "PAYMENT.CAPTURE.COMPLETED",
            {
                "id": "CAPFOREIGN",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "19.99"},
                "custom_id": self.order.reference,
                "supplementary_data": {"related_ids": {"order_id": "FOREIGNORDER"}},
            },
        )
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_replayed_webhook_changes_nothing(self):
        resource = {
            "id": "CAP1",
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "19.99"},
            "custom_id": self.order.reference,
            "supplementary_data": {"related_ids": {"order_id": self.paypal_order_id}},
        }
        self._post_webhook("PAYMENT.CAPTURE.COMPLETED", resource)
        response = self._post_webhook("PAYMENT.CAPTURE.COMPLETED", resource)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WebhookEvent.objects.filter(provider="paypal").count(), 1)
        self.assertEqual(Entitlement.objects.filter(user=self.buyer).count(), 1)

    def test_unverified_webhook_is_rejected_and_persists_nothing(self):
        self.fake.verify_result = "FAILURE"
        response = self._post_webhook(
            "PAYMENT.CAPTURE.COMPLETED",
            {
                "id": "CAP1",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "19.99"},
                "custom_id": self.order.reference,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WebhookEvent.objects.count(), 0)
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_webhook_without_signature_headers_is_rejected(self):
        response = self._post_webhook(
            "PAYMENT.CAPTURE.COMPLETED",
            {
                "id": "CAP1",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "1.00"},
            },
            headers={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_verification_outage_returns_503_so_paypal_retries(self):
        self.fake.verify_network_error = True
        response = self._post_webhook(
            "PAYMENT.CAPTURE.COMPLETED",
            {
                "id": "CAP1",
                "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "1.00"},
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_declined_capture_webhook_fails_the_attempt_only(self):
        self._post_webhook(
            "PAYMENT.CAPTURE.DECLINED",
            {
                "id": "CAP1",
                "status": "DECLINED",
                "amount": {"currency_code": "USD", "value": "19.99"},
                "custom_id": self.order.reference,
                "supplementary_data": {"related_ids": {"order_id": self.paypal_order_id}},
            },
        )
        self._refresh()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_status_check_pulls_an_approved_order_and_captures(self):
        self.fake.approve(self.paypal_order_id)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("marketplace:order_check_status", args=[self.order.reference]))
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)

    def test_pull_finds_a_capture_completed_while_our_process_was_down(self):
        self.fake.approve(self.paypal_order_id)
        Payment.objects.filter(pk=self.payment.pk).update(
            status=Payment.Status.PENDING, updated_at=timezone.now() - timedelta(minutes=10)
        )
        self.fake.capture_mode = "complete"
        self.fake._capture(self.paypal_order_id)
        with self.captureOnCommitCallbacks(execute=True):
            payments_service.reconcile_order(Order.objects.get(pk=self.order.pk))
        self._refresh()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(self.fake.calls_to("POST", "/capture"), [])


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["paypal"],
    SITE_URL="https://shop.example.test",
    MARKETPLACE_SUPPORT_EMAIL="ops@example.test",
    **PAYPAL_SETTINGS,
)
class PayPalRefundAndDisputeTests(TestCase):
    def setUp(self):
        cache.clear()
        paypal_provider._token_cache.clear()
        self.provider, self.fake = make_provider()
        patcher = mock.patch.dict(providers.PROVIDERS, {"paypal": self.provider})
        patcher.start()
        self.addCleanup(patcher.stop)
        MarketplaceSettings.objects.update_or_create(
            pk=1,
            defaults={
                "marketplace_enabled": True,
                "purchases_enabled": True,
                "fulfillment_enabled": True,
                "paypal_enabled": True,
            },
        )
        flags_service.invalidate()
        self.staff = factories.make_user("staff")
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product(unit_price_minor=1999)
        factories.make_keys(self.product, 3, self.staff)
        cart_service.add_item(self.buyer, self.product, 1)
        self.order, self.payment, self.intent = checkout_service.create_order(
            self.buyer, provider_slug="paypal"
        )
        self.paypal_order_id = self.intent.provider_payment_id
        self.client.login(username="buyer", password="testpass123")
        self.fake.approve(self.paypal_order_id)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(
                reverse("marketplace:order_detail", args=[self.order.reference]),
                {"token": self.paypal_order_id, "PayerID": "PAYER1"},
            )
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        assert self.order.status == Order.Status.FULFILLED
        assert self.payment.provider_reference == "CAP1"
        self.delivered_key = GameKey.objects.exclude(status=GameKey.Status.AVAILABLE).get(
            product=self.product
        )

    def post_webhook(self, event_type, resource, event_id="WH-1"):
        body = json.dumps({"id": event_id, "event_type": event_type, "resource": resource})
        extra = {"HTTP_" + k.replace("-", "_"): v for k, v in WEBHOOK_HEADERS.items()}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("marketplace:webhook", args=["paypal"]),
                data=body,
                content_type="application/json",
                **extra,
            )

    def alerts(self):
        return OutboundEmail.objects.filter(to_email="ops@example.test")

    def test_admin_refund_completes_synchronously(self):
        refund = refunds_service.request_refund(
            self.order, self.payment, self.order.total_minor, "", self.staff
        )
        refund = refunds_service.submit_refund(refund)
        self.assertEqual(refund.status, Refund.Status.COMPLETED)
        self.assertEqual(refund.provider_refund_id, "REFUND1")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.REFUNDED)
        self.delivered_key.refresh_from_db()
        self.assertEqual(self.delivered_key.status, GameKey.Status.REFUNDED)

    def _refund_resource(self, refund_id, capture_id, amount="5.00", status="COMPLETED"):
        # The real PAYMENT.CAPTURE.REFUNDED webhook resource is the refund
        # object itself: it has no capture_id field, only an "up" link back
        # to the capture (verified against a live sandbox payload).
        return {
            "id": refund_id,
            "amount": {"currency_code": "USD", "value": amount},
            "custom_id": self.order.reference,
            "status": status,
            "links": [
                {
                    "rel": "self",
                    "href": f"https://api.sandbox.paypal.com/v2/payments/refunds/{refund_id}",
                },
                {
                    "rel": "up",
                    "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}",
                },
            ],
        }

    def test_pending_refund_completes_on_the_capture_refunded_webhook(self):
        self.fake.refund_status = "PENDING"
        refund = refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)
        refund = refunds_service.submit_refund(refund)
        self.assertEqual(refund.status, Refund.Status.PROCESSING)

        self.fake.refunds[refund.provider_refund_id]["status"] = "COMPLETED"
        response = self.post_webhook(
            "PAYMENT.CAPTURE.REFUNDED",
            self._refund_resource(refund.provider_refund_id, "CAP1"),
            "WH-refund-1",
        )
        self.assertEqual(response.status_code, 200)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.COMPLETED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PARTIALLY_REFUNDED)

    def test_capture_refunded_with_no_matching_refund_is_recorded_as_provider_originated(self):
        response = self.post_webhook(
            "PAYMENT.CAPTURE.REFUNDED",
            self._refund_resource("REFUND-DASHBOARD", "CAP1"),
            "WH-refund-unexpected",
        )
        self.assertEqual(response.status_code, 200)
        refund = Refund.objects.get(provider_refund_id="REFUND-DASHBOARD")
        self.assertEqual(refund.source, Refund.Source.PROVIDER)
        self.assertEqual(refund.status, Refund.Status.COMPLETED)
        self.assertEqual(refund.amount_minor, 500)
        self.assertEqual(self.alerts().count(), 1)
        self.assertIn("outside GameKiro", self.alerts().get().subject)

    def test_capture_id_is_resolved_from_the_up_link_not_the_refund_id(self):
        # Regression test: the refund's own "id" must never be mistaken for
        # the capture id when binding the event to a Payment.
        provider = PayPalProvider()
        event = provider._event_from_payload(
            {
                "id": "WH-1",
                "event_type": "PAYMENT.CAPTURE.REFUNDED",
                "resource": self._refund_resource("R-1", "CAP1"),
            }
        )
        self.assertEqual(event.provider_reference, "CAP1")
        self.assertEqual(event.provider_refund_id, "R-1")
        self.assertNotEqual(event.provider_reference, event.provider_refund_id)

    def test_capture_reversed_is_treated_as_a_dispute_and_revokes_keys(self):
        response = self.post_webhook(
            "PAYMENT.CAPTURE.REVERSED",
            {"id": "CAP1", "custom_id": self.order.reference},
            "WH-reversed",
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CHARGEBACK)
        self.delivered_key.refresh_from_db()
        self.assertEqual(self.delivered_key.status, GameKey.Status.REVOKED)
        self.assertEqual(self.alerts().count(), 1)

    def test_chargeback_stage_dispute_opens_and_alerts_with_the_response_deadline(self):
        response = self.post_webhook(
            "CUSTOMER.DISPUTE.CREATED",
            {
                "dispute_id": "PP-D-1",
                "dispute_life_cycle_stage": "CHARGEBACK",
                "status": "OPEN",
                "reason": "UNAUTHORIZED",
                "disputed_transactions": [{"seller_transaction_id": "CAP1"}],
                "dispute_amount": {"currency_code": "USD", "value": "19.99"},
                "seller_response_due_date": "2026-10-05T00:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CHARGEBACK)
        dispute = Dispute.objects.get(provider="paypal", provider_dispute_id="PP-D-1")
        self.assertEqual(dispute.order, self.order)
        self.assertIsNotNone(dispute.response_due_at)
        self.delivered_key.refresh_from_db()
        self.assertEqual(self.delivered_key.status, GameKey.Status.REVOKED)
        self.assertEqual(self.alerts().count(), 1)

    def test_inquiry_stage_dispute_is_tracked_without_revoking_keys(self):
        response = self.post_webhook(
            "CUSTOMER.DISPUTE.CREATED",
            {
                "dispute_id": "PP-D-2",
                "dispute_life_cycle_stage": "INQUIRY",
                "status": "OPEN",
                "disputed_transactions": [{"seller_transaction_id": "CAP1"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertTrue(Dispute.objects.filter(provider_dispute_id="PP-D-2").exists())
        self.delivered_key.refresh_from_db()
        self.assertNotEqual(self.delivered_key.status, GameKey.Status.REVOKED)

    def test_dispute_resolved_records_the_outcome(self):
        self.post_webhook(
            "CUSTOMER.DISPUTE.CREATED",
            {
                "dispute_id": "PP-D-3",
                "dispute_life_cycle_stage": "CHARGEBACK",
                "status": "OPEN",
                "disputed_transactions": [{"seller_transaction_id": "CAP1"}],
            },
            "WH-d3-open",
        )
        response = self.post_webhook(
            "CUSTOMER.DISPUTE.RESOLVED",
            {
                "dispute_id": "PP-D-3",
                "dispute_life_cycle_stage": "CHARGEBACK",
                "status": "RESOLVED",
                "dispute_outcome": {"outcome_code": "RESOLVED_SELLER_FAVOUR"},
                "disputed_transactions": [{"seller_transaction_id": "CAP1"}],
            },
            "WH-d3-resolved",
        )
        self.assertEqual(response.status_code, 200)
        dispute = Dispute.objects.get(provider_dispute_id="PP-D-3")
        self.assertIsNotNone(dispute.closed_at)
        self.assertEqual(dispute.outcome, "RESOLVED_SELLER_FAVOUR")
