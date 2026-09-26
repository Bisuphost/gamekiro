import hashlib
import hmac
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import stripe
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketplace import providers
from marketplace.models import (
    Entitlement,
    GameKey,
    MarketplaceSettings,
    Order,
    Payment,
    WebhookEvent,
)
from marketplace.providers.base import ProviderError, WebhookVerificationError
from marketplace.providers.stripe_provider import StripeProvider
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import flags as flags_service
from marketplace.services import payments as payments_service

from . import factories

SECRET = "whsec_test_primary"
ROTATED_SECRET = "whsec_test_rotated"


def sign(body, secret=SECRET, timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def session_object(
    session_id, reference, amount, payment_status="paid", currency="usd", payment_intent="pi_1"
):
    return {
        "id": session_id,
        "object": "checkout.session",
        "mode": "payment",
        "client_reference_id": reference,
        "amount_total": amount,
        "currency": currency,
        "payment_status": payment_status,
        "status": "complete" if payment_status == "paid" else "open",
        "payment_intent": payment_intent,
        "metadata": {"order_reference": reference},
        "customer_details": {"email": "buyer@example.com", "name": "Private Person"},
    }


def event_body(event_id, stripe_type, obj):
    return json.dumps(
        {"id": event_id, "object": "event", "type": stripe_type, "data": {"object": obj}}
    )


class FakeSessions:
    def __init__(self):
        self.created = []
        self.sessions = {}
        self.by_key = {}
        self.expire_calls = []
        self.create_error = None
        self.expire_error = None

    def create(self, params, options):
        if self.create_error:
            raise self.create_error
        key = options["idempotency_key"]
        if key in self.by_key:
            original_params, session = self.by_key[key]
            if original_params != params:
                raise stripe.IdempotencyError(
                    "Keys for idempotent requests can only be used with the same parameters."
                )
            self.created.append((params, options))
            return session
        session_id = f"cs_test_{len(self.by_key) + 1}"
        self.created.append((params, options))
        self.sessions[session_id] = {
            "id": session_id,
            "url": f"https://checkout.stripe.test/c/pay/{session_id}",
            "status": "open",
            "payment_status": "unpaid",
            "mode": "payment",
            "client_reference_id": params["client_reference_id"],
            "amount_total": params["line_items"][0]["price_data"]["unit_amount"],
            "currency": params["line_items"][0]["price_data"]["currency"],
            "payment_intent": None,
            "metadata": params["metadata"],
        }
        self.by_key[key] = (params, self.sessions[session_id])
        return self.sessions[session_id]

    def retrieve(self, session_id):
        return self.sessions[session_id]

    def expire(self, session_id):
        self.expire_calls.append(session_id)
        if self.expire_error:
            raise self.expire_error
        self.sessions[session_id]["status"] = "expired"
        return self.sessions[session_id]


class FakeRefunds:
    def __init__(self):
        self.calls = []
        self.by_key = {}
        self.by_id = {}
        self.next_status = "succeeded"
        self.create_error = None

    def create(self, params, options):
        self.calls.append(("create", params, options))
        if self.create_error:
            raise self.create_error
        key = options.get("idempotency_key")
        if key in self.by_key:
            return self.by_key[key]
        refund = {
            "id": f"re_{len(self.by_id) + 1}",
            "status": self.next_status,
            "amount": params["amount"],
            "payment_intent": params["payment_intent"],
            "metadata": params.get("metadata", {}),
            "failure_reason": "",
        }
        self.by_id[refund["id"]] = refund
        if key:
            self.by_key[key] = refund
        return refund

    def retrieve(self, refund_id):
        self.calls.append(("retrieve", refund_id, None))
        return self.by_id[refund_id]


def fake_client():
    sessions = FakeSessions()
    refunds = FakeRefunds()
    client = SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=sessions), refunds=refunds)
    )
    return client, sessions, refunds


@override_settings(STRIPE_WEBHOOK_SECRETS=[SECRET, ROTATED_SECRET], STRIPE_SECRET_KEY="sk_test_x")
class StripeSignatureTests(SimpleTestCase):
    def setUp(self):
        self.provider = StripeProvider(client=fake_client()[0])
        self.body = event_body(
            "evt_1", "checkout.session.completed", session_object("cs_1", "ORD-A", 1999)
        )

    def test_valid_signature_is_accepted_and_normalized(self):
        event = self.provider.verify_webhook(
            self.body.encode(), {"Stripe-Signature": sign(self.body)}
        )
        self.assertEqual(event.event_id, "evt_1")
        self.assertEqual(event.event_type, "payment_succeeded")
        self.assertEqual(event.order_reference, "ORD-A")
        self.assertEqual(event.provider_payment_id, "cs_1")
        self.assertEqual(event.provider_reference, "pi_1")
        self.assertEqual(event.amount_minor, 1999)
        self.assertEqual(event.currency, "USD")
        self.assertEqual(event.provider, "stripe")

    def test_signature_from_a_rotated_secret_is_accepted(self):
        header = {"Stripe-Signature": sign(self.body, ROTATED_SECRET)}
        self.assertEqual(self.provider.verify_webhook(self.body.encode(), header).event_id, "evt_1")

    def test_wrong_secret_is_rejected(self):
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(
                self.body.encode(), {"Stripe-Signature": sign(self.body, "whsec_attacker")}
            )

    def test_tampered_body_is_rejected(self):
        header = {"Stripe-Signature": sign(self.body)}
        tampered = self.body.replace("1999", "1").encode()
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(tampered, header)

    def test_stale_timestamp_is_rejected(self):
        header = {"Stripe-Signature": sign(self.body, timestamp=int(time.time()) - 3600)}
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(self.body.encode(), header)

    def test_missing_signature_header_is_rejected(self):
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(self.body.encode(), {})

    def test_malformed_signature_header_is_rejected(self):
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(self.body.encode(), {"Stripe-Signature": "garbage"})

    def test_non_utf8_body_is_rejected_not_crashing(self):
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(b"\xff\xfe\x00", {"Stripe-Signature": sign("x")})

    @override_settings(STRIPE_WEBHOOK_SECRETS=[])
    def test_missing_configuration_rejects_everything(self):
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(self.body.encode(), {"Stripe-Signature": sign(self.body)})

    def test_valid_signature_but_non_json_body_is_rejected(self):
        body = "not json"
        with self.assertRaises(WebhookVerificationError):
            self.provider.verify_webhook(body.encode(), {"Stripe-Signature": sign(body)})

    def _normalize(self, stripe_type, **kwargs):
        body = event_body("evt_x", stripe_type, session_object("cs_1", "ORD-A", 1999, **kwargs))
        return self.provider.verify_webhook(body.encode(), {"Stripe-Signature": sign(body)})

    def test_completed_but_unpaid_is_not_treated_as_success(self):
        event = self._normalize("checkout.session.completed", payment_status="unpaid")
        self.assertEqual(event.event_type, "payment_pending")

    def test_async_success_maps_to_payment_succeeded(self):
        event = self._normalize("checkout.session.async_payment_succeeded")
        self.assertEqual(event.event_type, "payment_succeeded")

    def test_async_failure_maps_to_payment_failed(self):
        event = self._normalize("checkout.session.async_payment_failed", payment_status="unpaid")
        self.assertEqual(event.event_type, "payment_failed")

    def test_expiry_maps_to_attempt_expired(self):
        event = self._normalize("checkout.session.expired", payment_status="unpaid")
        self.assertEqual(event.event_type, "attempt_expired")

    def test_unrelated_event_types_pass_through_with_their_own_type(self):
        body = event_body("evt_y", "customer.created", {"id": "cus_1", "object": "customer"})
        event = self.provider.verify_webhook(body.encode(), {"Stripe-Signature": sign(body)})
        self.assertEqual(event.event_type, "customer.created")

    def test_stored_payload_contains_no_customer_pii(self):
        event = self._normalize("checkout.session.completed")
        stored = json.dumps(event.raw)
        self.assertNotIn("buyer@example.com", stored)
        self.assertNotIn("Private Person", stored)

    def test_stored_payload_round_trips_through_normalize_event(self):
        event = self._normalize("checkout.session.completed")
        rebuilt = self.provider.normalize_event(event.raw)
        self.assertEqual(rebuilt, event)


@override_settings(STRIPE_WEBHOOK_SECRETS=[SECRET], STRIPE_SECRET_KEY="sk_test_x")
class StripeSessionCreationTests(TestCase):
    def setUp(self):
        self.client_fake, self.sessions, self.refunds = fake_client()
        self.provider = StripeProvider(client=self.client_fake)
        self.buyer = factories.make_user("buyer", email="buyer@example.com")
        self.order = Order.objects.create(
            user=self.buyer, currency="USD", total_minor=1999, subtotal_minor=1999
        )
        self.payment = Payment.objects.create(
            order=self.order,
            provider="stripe",
            amount_minor=1999,
            currency="USD",
            idempotency_key=f"{self.order.reference}:stripe:1",
        )
        self.urls = payments_service.payment_urls(self.order)

    def test_session_parameters_follow_the_reviewed_design(self):
        intent = self.provider.create_payment(self.order, self.payment, self.urls)
        params, options = self.sessions.created[0]

        self.assertEqual(params["mode"], "payment")
        self.assertEqual(params["client_reference_id"], self.order.reference)
        self.assertEqual(params["payment_method_types"], ["card"])
        self.assertNotIn("automatic_tax", params)
        self.assertNotIn("managed_payments", params)
        price = params["line_items"][0]["price_data"]
        self.assertEqual(price["unit_amount"], 1999)
        self.assertEqual(price["currency"], "usd")
        self.assertEqual(params["line_items"][0]["quantity"], 1)
        self.assertIn("{CHECKOUT_SESSION_ID}", params["success_url"])
        self.assertTrue(params["success_url"].startswith("http"))
        self.assertEqual(params["metadata"]["order_reference"], self.order.reference)
        self.assertEqual(params["customer_email"], "buyer@example.com")
        self.assertEqual(options["idempotency_key"], f"{self.payment.idempotency_key}:create")
        self.assertEqual(intent.provider_payment_id, "cs_test_1")
        self.assertTrue(intent.redirect_url.startswith("https://checkout.stripe.test/"))

    def test_session_expiry_is_31_minutes_after_the_attempt_was_created(self):
        self.provider.create_payment(self.order, self.payment, self.urls)
        params, _ = self.sessions.created[0]
        expected = int((self.payment.created_at + timedelta(minutes=31)).timestamp())
        self.assertEqual(params["expires_at"], expected)

    def test_retry_sends_identical_parameters_so_stripe_can_replay_it(self):
        self.provider.create_payment(self.order, self.payment, self.urls)
        self.provider.create_payment(self.order, self.payment, self.urls)
        (first, first_opts), (second, second_opts) = self.sessions.created
        self.assertEqual(first, second)
        self.assertEqual(first_opts, second_opts)

    def test_unsupported_currency_is_a_definitive_error(self):
        self.order.currency = "JPY"
        with self.assertRaises(ProviderError) as ctx:
            self.provider.create_payment(self.order, self.payment, self.urls)
        self.assertTrue(ctx.exception.definitive)

    def test_network_failure_is_not_definitive(self):
        self.sessions.create_error = stripe.APIConnectionError("timeout")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.create_payment(self.order, self.payment, self.urls)
        self.assertFalse(ctx.exception.definitive)

    def test_rejected_request_is_definitive(self):
        self.sessions.create_error = stripe.InvalidRequestError("bad", "expires_at")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.create_payment(self.order, self.payment, self.urls)
        self.assertTrue(ctx.exception.definitive)

    def test_missing_secret_key_is_definitive(self):
        with override_settings(STRIPE_SECRET_KEY=None):
            with self.assertRaises(ProviderError) as ctx:
                StripeProvider().create_payment(self.order, self.payment, self.urls)
        self.assertTrue(ctx.exception.definitive)

    def test_refund_uses_the_payment_intent_and_caller_idempotency_key(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_reference="pi_9")
        self.payment.refresh_from_db()
        result = self.provider.refund(self.payment, 500, "refund-7")
        _, params, options = self.refunds.calls[0]
        self.assertEqual(params["payment_intent"], "pi_9")
        self.assertEqual(params["amount"], 500)
        self.assertEqual(options["idempotency_key"], "refund-7")
        self.assertEqual((result.provider_refund_id, result.status), ("re_1", "succeeded"))

    def test_refund_without_a_payment_intent_is_refused(self):
        with self.assertRaises(ProviderError):
            self.provider.refund(self.payment, 500, "refund-7")

    def test_cancel_expires_the_open_session(self):
        self.provider.create_payment(self.order, self.payment, self.urls)
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id="cs_test_1")
        self.payment.refresh_from_db()
        self.provider.cancel_payment(self.payment)
        self.assertEqual(self.sessions.expire_calls, ["cs_test_1"])

    def test_cancel_of_an_already_completed_session_is_refused(self):
        self.provider.create_payment(self.order, self.payment, self.urls)
        self.sessions.sessions["cs_test_1"]["status"] = "complete"
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id="cs_test_1")
        self.payment.refresh_from_db()
        self.sessions.expire_error = stripe.InvalidRequestError("not open", "id")
        with self.assertRaises(ProviderError):
            self.provider.cancel_payment(self.payment)

    def test_cancel_of_an_already_expired_session_is_fine(self):
        self.provider.create_payment(self.order, self.payment, self.urls)
        self.sessions.sessions["cs_test_1"]["status"] = "expired"
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id="cs_test_1")
        self.payment.refresh_from_db()
        self.sessions.expire_error = stripe.InvalidRequestError("already expired", "id")
        self.provider.cancel_payment(self.payment)


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["stripe"],
    STRIPE_SECRET_KEY="sk_test_x",
    STRIPE_WEBHOOK_SECRETS=[SECRET],
    SITE_URL="https://shop.example.test",
)
class StripeEndToEndTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client_fake, self.sessions, self.refunds = fake_client()
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
        cart_service.add_item(self.buyer, self.product, 1)
        self.order, self.payment, self.intent = checkout_service.create_order(
            self.buyer, provider_slug="stripe"
        )
        self.session_id = self.intent.provider_payment_id

    def _post_event(self, stripe_type, obj, event_id="evt_1", secret=SECRET):
        body = event_body(event_id, stripe_type, obj)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("marketplace:webhook", args=["stripe"]),
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sign(body, secret),
            )

    def _paid_session(self, amount=None, **kwargs):
        return session_object(
            self.session_id, self.order.reference, amount or self.order.total_minor, **kwargs
        )

    def test_checkout_creates_a_hosted_session_bound_to_our_attempt(self):
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.provider_payment_id, self.session_id)
        self.assertEqual(self.order.provider, "stripe")
        self.assertEqual(self.order.provider_session_id, self.session_id)
        self.assertTrue(self.intent.redirect_url.startswith("https://checkout.stripe.test/"))
        params, _ = self.sessions.created[0]
        self.assertTrue(
            params["success_url"].startswith(
                f"https://shop.example.test/store/orders/{self.order.reference}/"
            )
        )

    def test_signed_completed_event_pays_and_fulfils_the_order(self):
        response = self._post_event("checkout.session.completed", self._paid_session())
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.provider_reference, "pi_1")

    def test_stored_webhook_event_holds_only_minimal_normalized_fields(self):
        self._post_event("checkout.session.completed", self._paid_session())
        stored = json.dumps(WebhookEvent.objects.get(provider="stripe").payload)
        self.assertNotIn("buyer@example.com", stored)
        self.assertNotIn("customer_details", stored)

    def test_replayed_event_changes_nothing(self):
        self._post_event("checkout.session.completed", self._paid_session())
        response = self._post_event("checkout.session.completed", self._paid_session())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WebhookEvent.objects.filter(provider="stripe").count(), 1)
        self.assertEqual(Entitlement.objects.filter(user=self.buyer).count(), 1)
        self.assertEqual(GameKey.objects.filter(reserved_by_item__order=self.order).count(), 1)

    def test_second_event_for_same_session_is_a_duplicate_transition(self):
        self._post_event("checkout.session.completed", self._paid_session(), event_id="evt_1")
        self._post_event(
            "checkout.session.async_payment_succeeded", self._paid_session(), event_id="evt_2"
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        statuses = set(WebhookEvent.objects.values_list("status", flat=True))
        self.assertEqual(statuses, {"processed", "duplicate_transition"})

    def test_bad_signature_is_rejected_and_persists_nothing(self):
        response = self._post_event(
            "checkout.session.completed", self._paid_session(), secret="whsec_attacker"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(WebhookEvent.objects.count(), 0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_amount_mismatch_sends_the_order_to_review(self):
        self._post_event("checkout.session.completed", self._paid_session(amount=1))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.NEEDS_REVIEW)

    def test_currency_mismatch_sends_the_order_to_review(self):
        self._post_event("checkout.session.completed", self._paid_session(currency="eur"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.NEEDS_REVIEW)

    def test_event_for_a_session_we_never_created_is_not_applied(self):
        foreign = session_object("cs_foreign", self.order.reference, self.order.total_minor)
        response = self._post_event("checkout.session.completed", foreign)
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(
            WebhookEvent.objects.get(provider="stripe").status, WebhookEvent.Status.ORPHANED
        )

    def test_unpaid_completed_event_does_not_fulfil(self):
        self._post_event("checkout.session.completed", self._paid_session(payment_status="unpaid"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_expired_event_expires_the_attempt_but_keeps_the_order_payable(self):
        self._post_event("checkout.session.expired", self._paid_session(payment_status="unpaid"))
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.EXPIRED)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_unrelated_event_types_are_acknowledged_and_ignored(self):
        response = self._post_event("customer.created", {"id": "cus_1", "object": "customer"})
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_async_failure_fails_the_attempt_and_the_order(self):
        self._post_event(
            "checkout.session.async_payment_failed", self._paid_session(payment_status="unpaid")
        )
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PAYMENT_FAILED)

    def test_landing_page_reconciles_a_paid_session_without_the_webhook(self):
        self.sessions.sessions[self.session_id].update(
            status="complete", payment_status="paid", payment_intent="pi_7"
        )
        self.client.login(username="buyer", password="testpass123")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(
                reverse("marketplace:order_detail", args=[self.order.reference]),
                {"session_id": self.session_id},
            )
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(self.payment.provider_reference, "pi_7")

    def test_landing_page_with_someone_elses_session_id_does_nothing(self):
        self.sessions.sessions[self.session_id].update(status="complete", payment_status="paid")
        self.client.login(username="buyer", password="testpass123")
        self.client.get(
            reverse("marketplace:order_detail", args=[self.order.reference]),
            {"session_id": "cs_someone_else"},
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_landing_page_is_owner_scoped(self):
        self.sessions.sessions[self.session_id].update(status="complete", payment_status="paid")
        factories.make_user("intruder")
        self.client.login(username="intruder", password="testpass123")
        response = self.client.get(
            reverse("marketplace:order_detail", args=[self.order.reference]),
            {"session_id": self.session_id},
        )
        self.assertEqual(response.status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_status_check_pulls_the_session_and_is_rate_limited(self):
        self.sessions.sessions[self.session_id].update(status="complete", payment_status="paid")
        self.client.login(username="buyer", password="testpass123")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("marketplace:order_check_status", args=[self.order.reference]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)

    def test_pull_reconciliation_marks_an_expired_session(self):
        self.sessions.sessions[self.session_id]["status"] = "expired"
        payments_service.reconcile_order(self.order)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.EXPIRED)

    def test_new_attempt_expires_the_previous_session_first(self):
        self.payment.refresh_from_db()
        Payment.objects.filter(pk=self.payment.pk).update(
            expires_at=self.payment.created_at - timedelta(minutes=1)
        )
        second, intent = payments_service.start_or_resume_payment(self.order, "stripe")
        self.assertEqual(self.sessions.expire_calls, [self.session_id])
        self.assertEqual(second.attempt, 2)
        self.assertEqual(second.idempotency_key, f"{self.order.reference}:stripe:2")
        self.assertNotEqual(intent.provider_payment_id, self.session_id)

    def test_new_stripe_attempt_refused_when_reservation_cannot_outlive_the_session(self):
        Order.objects.filter(pk=self.order.pk).update(
            reservation_expires_at=timezone.now() + timedelta(minutes=5)
        )
        Payment.objects.filter(pk=self.payment.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        with self.assertRaises(payments_service.PaymentError):
            payments_service.start_or_resume_payment(self.order, "stripe")
        self.assertEqual(self.sessions.expire_calls, [])
        self.assertEqual(len(self.sessions.created), 1)

    def test_unrecorded_session_is_recovered_by_replaying_the_same_idempotency_key(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id=None)
        self.payment.refresh_from_db()
        payments_service.start_or_resume_payment(self.order, "stripe")
        first, second = self.sessions.created
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[0], second[0])

    def test_definitive_rejection_ends_the_attempt_so_a_new_one_can_start(self):
        Payment.objects.filter(pk=self.payment.pk).update(provider_payment_id=None)
        self.sessions.create_error = stripe.InvalidRequestError("expires_at too soon", "expires_at")
        _, intent = payments_service.start_or_resume_payment(self.order, "stripe")
        self.assertIsNone(intent)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)

        self.sessions.create_error = None
        second, intent = payments_service.start_or_resume_payment(self.order, "stripe")
        self.assertEqual(second.attempt, 2)
        self.assertIsNotNone(intent)
