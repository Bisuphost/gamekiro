from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketplace import providers
from marketplace.models import (
    Dispute,
    GameKey,
    MarketplaceSettings,
    Order,
    OutboundEmail,
    Payment,
    Refund,
)
from marketplace.providers.stripe_provider import StripeProvider
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import flags as flags_service
from marketplace.services import jobs as jobs_service
from marketplace.services import refunds as refunds_service

from . import factories
from .test_stripe_provider import SECRET, event_body, fake_client, session_object, sign


def refund_object(
    refund_id,
    payment_intent,
    amount,
    currency="usd",
    status="succeeded",
    order_reference="",
    refund_pk=None,
):
    metadata = {}
    if order_reference:
        metadata["order_reference"] = order_reference
    if refund_pk is not None:
        metadata["refund_id"] = str(refund_pk)
    return {
        "id": refund_id,
        "object": "refund",
        "amount": amount,
        "currency": currency,
        "payment_intent": payment_intent,
        "status": status,
        "metadata": metadata,
        "failure_reason": "" if status != "failed" else "expired_or_canceled_card",
    }


def dispute_object(
    dispute_id, payment_intent, amount, currency="usd", status="warning_needs_response", due_by=None
):
    return {
        "id": dispute_id,
        "object": "dispute",
        "amount": amount,
        "currency": currency,
        "payment_intent": payment_intent,
        "reason": "fraudulent",
        "status": status,
        "evidence_details": {"due_by": due_by},
    }


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    PAYMENT_PROVIDERS_ENABLED=["stripe"],
    STRIPE_SECRET_KEY="sk_test_x",
    STRIPE_WEBHOOK_SECRETS=[SECRET],
    MARKETPLACE_SUPPORT_EMAIL="ops@example.test",
)
class RefundRedesignTestCase(TestCase):
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
        self.product = factories.make_product(unit_price_minor=2000)
        factories.make_keys(self.product, 3, self.staff)
        cart_service.add_item(self.buyer, self.product, 1)
        self.order, self.payment, self.intent = checkout_service.create_order(
            self.buyer, provider_slug="stripe"
        )
        self.session_id = self.intent.provider_payment_id

    def pay(self):
        obj = session_object(self.session_id, self.order.reference, self.order.total_minor)
        body = event_body("evt_pay", "checkout.session.completed", obj)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("marketplace:webhook", args=["stripe"]),
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sign(body),
            )
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        assert self.order.status == Order.Status.FULFILLED
        assert self.payment.provider_reference == "pi_1"
        self.delivered_key = GameKey.objects.exclude(status=GameKey.Status.AVAILABLE).get(
            product=self.product
        )

    def post_webhook(self, event_type, obj, event_id):
        body = event_body(event_id, event_type, obj)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("marketplace:webhook", args=["stripe"]),
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sign(body),
            )

    def alerts(self):
        return OutboundEmail.objects.filter(to_email="ops@example.test")


class WriteAheadAndAmountGuardTests(RefundRedesignTestCase):
    def test_deterministic_idempotency_key_and_write_ahead_row(self):
        self.pay()
        refund = refunds_service.request_refund(self.order, self.payment, 500, "test", self.staff)
        self.assertEqual(refund.idempotency_key, f"refund-{refund.pk}")
        self.assertEqual(refund.status, Refund.Status.REQUESTED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_minor, 500)

    def test_requesting_more_than_refundable_raises_and_reserves_nothing(self):
        self.pay()
        with self.assertRaises(refunds_service.RefundError):
            refunds_service.request_refund(self.order, self.payment, 2500, "", self.staff)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_minor, 0)
        self.assertEqual(Refund.objects.count(), 0)

    def test_two_partial_refunds_cannot_together_exceed_the_amount(self):
        self.pay()
        refunds_service.request_refund(self.order, self.payment, 1500, "", self.staff)
        with self.assertRaises(refunds_service.RefundError):
            refunds_service.request_refund(self.order, self.payment, 600, "", self.staff)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_minor, 1500)

    def test_second_partial_refund_up_to_the_remainder_is_allowed(self):
        self.pay()
        refunds_service.request_refund(self.order, self.payment, 1500, "", self.staff)
        refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_minor, 2000)

    def test_zero_or_negative_amount_is_rejected(self):
        self.pay()
        for bad in (0, -100):
            with self.subTest(bad=bad), self.assertRaises(refunds_service.RefundError):
                refunds_service.request_refund(self.order, self.payment, bad, "", self.staff)

    def test_unpaid_payment_cannot_be_refunded(self):
        with self.assertRaises(refunds_service.RefundError):
            refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)


class SynchronousAndAsyncRefundTests(RefundRedesignTestCase):
    def test_provider_accepts_synchronously_and_completes_immediately(self):
        self.pay()
        self.refunds.next_status = "succeeded"
        refund = refunds_service.request_refund(
            self.order, self.payment, self.order.total_minor, "", self.staff
        )
        refund = refunds_service.submit_refund(refund)
        self.assertEqual(refund.status, Refund.Status.COMPLETED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.REFUNDED)
        self.delivered_key.refresh_from_db()
        self.assertEqual(self.delivered_key.status, GameKey.Status.REFUNDED)

    def test_partial_refund_marks_order_partially_refunded_and_never_touches_keys(self):
        self.pay()
        refund = refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)
        refunds_service.submit_refund(refund)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PARTIALLY_REFUNDED)
        self.delivered_key.refresh_from_db()
        self.assertNotEqual(self.delivered_key.status, GameKey.Status.REFUNDED)
        self.assertNotEqual(self.delivered_key.status, GameKey.Status.AVAILABLE)

    def test_pending_provider_response_does_not_refund_or_touch_the_order_yet(self):
        self.pay()
        self.refunds.next_status = "pending"
        refund = refunds_service.request_refund(
            self.order, self.payment, self.order.total_minor, "", self.staff
        )
        refund = refunds_service.submit_refund(refund)
        self.assertEqual(refund.status, Refund.Status.PROCESSING)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)

    def test_async_success_webhook_completes_a_pending_refund(self):
        self.pay()
        self.refunds.next_status = "pending"
        refund = refunds_service.request_refund(
            self.order, self.payment, self.order.total_minor, "", self.staff
        )
        refund = refunds_service.submit_refund(refund)

        obj = refund_object(
            refund.provider_refund_id,
            "pi_1",
            self.order.total_minor,
            order_reference=self.order.reference,
        )
        obj["status"] = "succeeded"
        response = self.post_webhook("refund.updated", obj, "evt_refund_ok")

        self.assertEqual(response.status_code, 200)
        refund.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.COMPLETED)
        self.assertEqual(self.order.status, Order.Status.REFUNDED)

    def test_async_failure_webhook_fails_the_refund_and_releases_the_reservation(self):
        self.pay()
        self.refunds.next_status = "pending"
        refund = refunds_service.request_refund(self.order, self.payment, 700, "", self.staff)
        refund = refunds_service.submit_refund(refund)

        obj = refund_object(
            refund.provider_refund_id,
            "pi_1",
            700,
            status="failed",
            order_reference=self.order.reference,
        )
        response = self.post_webhook("refund.failed", obj, "evt_refund_fail")

        self.assertEqual(response.status_code, 200)
        refund.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.FAILED)
        self.assertIn("expired_or_canceled_card", refund.failure_reason)
        self.assertEqual(self.payment.refunded_minor, 0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.FULFILLED)
        self.assertEqual(self.alerts().count(), 1)

    def test_replayed_refund_webhook_changes_nothing_further(self):
        self.pay()
        self.refunds.next_status = "pending"
        refund = refunds_service.request_refund(
            self.order, self.payment, self.order.total_minor, "", self.staff
        )
        refund = refunds_service.submit_refund(refund)
        obj = refund_object(
            refund.provider_refund_id,
            "pi_1",
            self.order.total_minor,
            order_reference=self.order.reference,
        )
        obj["status"] = "succeeded"
        self.post_webhook("refund.updated", obj, "evt_a")
        response = self.post_webhook("refund.updated", obj, "evt_b")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.alerts().count(), 0)

    def test_failed_refund_can_be_retried_as_a_new_refund(self):
        self.pay()
        self.refunds.create_error = None
        self.refunds.next_status = "pending"
        first = refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)
        first = refunds_service.submit_refund(first)
        self.post_webhook(
            "refund.failed",
            refund_object(first.provider_refund_id, "pi_1", 500, status="failed"),
            "evt_fail",
        )
        self.refunds.next_status = "succeeded"
        second = refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)
        second = refunds_service.submit_refund(second)
        self.assertEqual(second.status, Refund.Status.COMPLETED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_minor, 500)


class ProviderOriginatedRefundTests(RefundRedesignTestCase):
    def test_refund_issued_in_the_stripe_dashboard_is_recorded_and_alerted(self):
        self.pay()
        obj = refund_object("re_dashboard", "pi_1", 800, order_reference="")
        response = self.post_webhook("refund.updated", obj, "evt_dashboard")

        self.assertEqual(response.status_code, 200)
        refund = Refund.objects.get(provider_refund_id="re_dashboard")
        self.assertEqual(refund.source, Refund.Source.PROVIDER)
        self.assertEqual(refund.status, Refund.Status.COMPLETED)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.refunded_minor, 800)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PARTIALLY_REFUNDED)
        self.assertEqual(self.alerts().count(), 1)

    def test_dashboard_refund_larger_than_remaining_is_flagged_not_silently_dropped(self):
        self.pay()
        refunds_service.request_refund(self.order, self.payment, 1900, "", self.staff)
        obj = refund_object("re_dashboard", "pi_1", 500, order_reference="")
        response = self.post_webhook("refund.updated", obj, "evt_over")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Refund.objects.filter(provider_refund_id="re_dashboard").exists())
        self.assertEqual(self.alerts().count(), 1)
        self.assertIn("exceeds", self.alerts().get().subject)

    def test_capture_refunded_with_no_open_admin_refund_alerts_for_manual_reconciliation(self):
        self.pay()
        response = self.post_webhook(
            "charge.refunded",
            {**session_object(self.session_id, self.order.reference, 2000)},
            "evt_cr",
        )
        self.assertEqual(response.status_code, 200)


class DisputeTests(RefundRedesignTestCase):
    def test_dispute_created_opens_a_record_transitions_and_alerts_with_deadline(self):
        self.pay()
        due = "2026-10-01T00:00:00Z"
        obj = dispute_object("dp_1", "pi_1", 2000, due_by=due)
        response = self.post_webhook("charge.dispute.created", obj, "evt_dispute")

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CHARGEBACK)
        dispute = Dispute.objects.get(provider="stripe", provider_dispute_id="dp_1")
        self.assertEqual(dispute.order, self.order)
        self.assertIsNotNone(dispute.response_due_at)
        self.delivered_key.refresh_from_db()
        self.assertEqual(self.delivered_key.status, GameKey.Status.REVOKED)
        self.assertEqual(self.alerts().count(), 1)
        self.assertIn("Dispute opened", self.alerts().get().subject)

    def test_dispute_updated_does_not_alert_or_transition_again(self):
        self.pay()
        self.post_webhook("charge.dispute.created", dispute_object("dp_1", "pi_1", 2000), "evt_1")
        response = self.post_webhook(
            "charge.dispute.updated",
            dispute_object("dp_1", "pi_1", 2000, status="under_review"),
            "evt_2",
        )
        self.assertEqual(response.status_code, 200)
        dispute = Dispute.objects.get(provider_dispute_id="dp_1")
        self.assertEqual(dispute.status, "under_review")
        self.assertEqual(self.alerts().count(), 1)

    def test_dispute_closed_records_the_outcome_and_alerts(self):
        self.pay()
        self.post_webhook("charge.dispute.created", dispute_object("dp_1", "pi_1", 2000), "evt_1")
        closed = dispute_object("dp_1", "pi_1", 2000, status="lost")
        response = self.post_webhook("charge.dispute.closed", closed, "evt_2")
        self.assertEqual(response.status_code, 200)
        dispute = Dispute.objects.get(provider_dispute_id="dp_1")
        self.assertIsNotNone(dispute.closed_at)
        self.assertEqual(self.alerts().count(), 2)

    def test_keys_revoked_by_a_dispute_are_not_restored_when_it_closes_in_our_favor(self):
        self.pay()
        self.post_webhook("charge.dispute.created", dispute_object("dp_1", "pi_1", 2000), "evt_1")
        won = dispute_object("dp_1", "pi_1", 2000, status="won")
        self.post_webhook("charge.dispute.closed", won, "evt_2")
        self.delivered_key.refresh_from_db()
        self.assertEqual(self.delivered_key.status, GameKey.Status.REVOKED)

    def test_replayed_dispute_created_does_not_alert_twice(self):
        self.pay()
        obj = dispute_object("dp_1", "pi_1", 2000)
        self.post_webhook("charge.dispute.created", obj, "evt_1")
        self.post_webhook("charge.dispute.created", obj, "evt_2")
        self.assertEqual(self.alerts().count(), 1)


class RefundReconciliationJobTests(RefundRedesignTestCase):
    def test_stale_processing_refund_is_resubmitted_and_settles(self):
        self.pay()
        self.refunds.next_status = "pending"
        refund = refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)
        refund = refunds_service.submit_refund(refund)
        Refund.objects.filter(pk=refund.pk).update(
            updated_at=timezone.now() - timedelta(minutes=10)
        )
        self.refunds.by_id[refund.provider_refund_id]["status"] = "succeeded"

        result = jobs_service.reconcile_refunds()

        self.assertEqual(result["settled"], 1)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.COMPLETED)

    def test_recent_processing_refunds_are_left_alone(self):
        self.pay()
        self.refunds.next_status = "pending"
        refund = refunds_service.request_refund(self.order, self.payment, 500, "", self.staff)
        refunds_service.submit_refund(refund)
        result = jobs_service.reconcile_refunds()
        self.assertEqual(result["checked"], 0)

    def test_mock_provider_refunds_are_not_polled(self):
        mock_payment = Payment.objects.create(
            order=self.order,
            provider="mock",
            attempt=99,
            amount_minor=500,
            currency="USD",
            idempotency_key="mock-refund-poll",
            status=Payment.Status.PAID,
        )
        refund = refunds_service.request_refund(self.order, mock_payment, 500, "", self.staff)
        Refund.objects.filter(pk=refund.pk).update(
            status=Refund.Status.PROCESSING, updated_at=timezone.now() - timedelta(minutes=10)
        )
        result = jobs_service.reconcile_refunds()
        self.assertEqual(result["checked"], 0)
