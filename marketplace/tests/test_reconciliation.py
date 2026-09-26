from datetime import timedelta
from unittest import mock

import stripe
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from marketplace import providers
from marketplace.models import (
    Entitlement,
    MarketplaceSettings,
    Order,
    OrderAuditLog,
    OutboundEmail,
    Payment,
    WebhookEvent,
)
from marketplace.providers.stripe_provider import StripeProvider
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import flags as flags_service
from marketplace.services import jobs as jobs_service

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
class ReconciliationTestCase(TestCase):
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

    def post_event(self, stripe_type, obj, event_id):
        body = event_body(event_id, stripe_type, obj)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("marketplace:webhook", args=["stripe"]),
                data=body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sign(body),
            )

    def age(self, payment, minutes):
        Payment.objects.filter(pk=payment.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes)
        )

    def alerts(self):
        return OutboundEmail.objects.filter(to_email="ops@example.test")


class UnexpectedPaymentTests(ReconciliationTestCase):
    def test_second_payment_on_a_paid_order_is_recorded_and_alerted_not_delivered(self):
        order, first, first_session = self.make_order()
        self.post_event(
            "checkout.session.completed",
            session_object(first_session, order.reference, order.total_minor),
            "evt_first",
        )
        second = Payment.objects.create(
            order=order,
            provider="stripe",
            attempt=2,
            amount_minor=order.total_minor,
            currency="USD",
            idempotency_key=f"{order.reference}:stripe:2",
            provider_payment_id="cs_second",
        )

        response = self.post_event(
            "checkout.session.completed",
            session_object("cs_second", order.reference, order.total_minor, payment_intent="pi_2"),
            "evt_second",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertEqual(second.status, Payment.Status.PAID)
        self.assertEqual(second.provider_reference, "pi_2")
        self.assertEqual(Entitlement.objects.filter(user=self.buyer).count(), 1)
        self.assertTrue(
            OrderAuditLog.objects.filter(order=order, action="unexpected_payment").exists()
        )
        self.assertEqual(self.alerts().count(), 1)
        self.assertIn(order.reference, self.alerts().get().body_text)

    def test_a_second_event_for_the_same_extra_payment_does_not_alert_again(self):
        order, _, first_session = self.make_order()
        self.post_event(
            "checkout.session.completed",
            session_object(first_session, order.reference, order.total_minor),
            "evt_first",
        )
        Payment.objects.create(
            order=order,
            provider="stripe",
            attempt=2,
            amount_minor=order.total_minor,
            currency="USD",
            idempotency_key=f"{order.reference}:stripe:2",
            provider_payment_id="cs_second",
        )
        obj = session_object("cs_second", order.reference, order.total_minor, payment_intent="pi_2")
        self.post_event("checkout.session.completed", obj, "evt_second")
        self.post_event("checkout.session.async_payment_succeeded", obj, "evt_third")
        self.assertEqual(self.alerts().count(), 1)

    def test_normal_duplicate_event_for_the_paying_session_does_not_alert(self):
        order, _, session_id = self.make_order()
        obj = session_object(session_id, order.reference, order.total_minor)
        self.post_event("checkout.session.completed", obj, "evt_a")
        self.post_event("checkout.session.async_payment_succeeded", obj, "evt_b")
        self.assertEqual(self.alerts().count(), 0)

    def test_payment_arriving_for_a_failed_order_is_alerted(self):
        order, payment, session_id = self.make_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAYMENT_FAILED)
        Payment.objects.filter(pk=payment.pk).update(status=Payment.Status.FAILED)

        self.post_event(
            "checkout.session.completed",
            session_object(session_id, order.reference, order.total_minor),
            "evt_late",
        )

        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAYMENT_FAILED)
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(Entitlement.objects.count(), 0)
        self.assertEqual(self.alerts().count(), 1)

    def test_amount_mismatch_records_the_payment_and_alerts_once(self):
        order, payment, session_id = self.make_order()
        obj = session_object(session_id, order.reference, 1)
        self.post_event("checkout.session.completed", obj, "evt_a")
        self.post_event("checkout.session.async_payment_succeeded", obj, "evt_b")
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, Order.Status.NEEDS_REVIEW)
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(self.alerts().count(), 1)


class PullReconciliationTests(ReconciliationTestCase):
    def test_paid_session_is_found_and_fulfilled_without_any_webhook(self):
        order, payment, session_id = self.make_order()
        self.sessions.sessions[session_id].update(
            status="complete", payment_status="paid", payment_intent="pi_pull"
        )
        self.age(payment, 10)

        with self.captureOnCommitCallbacks(execute=True):
            result = jobs_service.pull_reconciliation()

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["changed"], 1)

    def test_recent_attempts_are_left_alone(self):
        order, payment, session_id = self.make_order()
        self.sessions.sessions[session_id].update(status="complete", payment_status="paid")
        result = jobs_service.pull_reconciliation()
        self.assertEqual(result["checked"], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

    def test_an_attempt_is_not_rechecked_within_the_recheck_window(self):
        order, payment, _ = self.make_order()
        self.age(payment, 10)
        first = jobs_service.pull_reconciliation()
        second = jobs_service.pull_reconciliation()
        self.assertEqual((first["checked"], second["checked"]), (1, 0))
        Payment.objects.filter(pk=payment.pk).update(
            reconciled_at=timezone.now() - timedelta(minutes=5)
        )
        self.assertEqual(jobs_service.pull_reconciliation()["checked"], 1)

    def test_orders_that_are_no_longer_pending_are_skipped(self):
        order, payment, _ = self.make_order()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.EXPIRED)
        self.age(payment, 10)
        self.assertEqual(jobs_service.pull_reconciliation()["checked"], 0)

    def test_provider_errors_are_counted_not_raised(self):
        order, payment, _ = self.make_order()
        self.age(payment, 10)
        with mock.patch.object(
            self.client_fake.v1.checkout.sessions,
            "retrieve",
            side_effect=stripe.APIConnectionError("down"),
        ):
            result = jobs_service.pull_reconciliation()
        self.assertEqual(result["errors"], 1)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

    def test_open_session_causes_no_change(self):
        order, payment, _ = self.make_order()
        self.age(payment, 10)
        result = jobs_service.pull_reconciliation()
        self.assertEqual((result["checked"], result["changed"]), (1, 0))

    def test_unrecorded_session_is_recovered_by_replay_and_the_parked_event_then_applies(self):
        order, payment, session_id = self.make_order()
        Payment.objects.filter(pk=payment.pk).update(provider_payment_id=None)
        Order.objects.filter(pk=order.pk).update(provider_session_id=None)
        self.age(payment, 10)
        key = f"{payment.idempotency_key}:create"
        original_params, session = self.sessions.by_key[key]
        self.sessions.by_key[key] = (
            {**original_params, "expires_at": original_params["expires_at"] - 600},
            session,
        )

        self.post_event(
            "checkout.session.completed",
            session_object(session_id, order.reference, order.total_minor),
            "evt_parked",
        )
        parked = WebhookEvent.objects.get(event_id="evt_parked")
        self.assertEqual(parked.status, WebhookEvent.Status.ORPHANED)
        self.assertIsNone(parked.processed_at)

        result = jobs_service.pull_reconciliation()
        self.assertEqual(result["recovered"], 1)
        payment.refresh_from_db()
        self.assertEqual(payment.provider_payment_id, session_id)

        with self.captureOnCommitCallbacks(execute=True):
            jobs_service.reconcile_stuck_webhooks()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)


class StuckWebhookTests(ReconciliationTestCase):
    def test_event_that_failed_processing_is_retried_by_the_provider_and_by_cron(self):
        order, payment, session_id = self.make_order()
        obj = session_object(session_id, order.reference, order.total_minor)

        with mock.patch(
            "marketplace.services.payments.apply_payment_confirmation",
            side_effect=RuntimeError("database hiccup"),
        ):
            response = self.post_event("checkout.session.completed", obj, "evt_redrive")

        self.assertEqual(response.status_code, 500)
        row = WebhookEvent.objects.get(event_id="evt_redrive")
        self.assertEqual(row.status, WebhookEvent.Status.FAILED)
        self.assertIsNone(row.processed_at)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)

        with self.captureOnCommitCallbacks(execute=True):
            result = jobs_service.reconcile_stuck_webhooks()

        self.assertEqual(result["redriven"], 1)
        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertEqual(payment.status, Payment.Status.PAID)
        row.refresh_from_db()
        self.assertIsNotNone(row.processed_at)

    def test_provider_redelivery_after_a_500_is_processed(self):
        order, _, session_id = self.make_order()
        obj = session_object(session_id, order.reference, order.total_minor)
        with mock.patch(
            "marketplace.services.payments.apply_payment_confirmation",
            side_effect=RuntimeError("database hiccup"),
        ):
            first = self.post_event("checkout.session.completed", obj, "evt_retry")
        second = self.post_event("checkout.session.completed", obj, "evt_retry")
        self.assertEqual(first.status_code, 500)
        self.assertEqual(second.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)
        self.assertEqual(WebhookEvent.objects.filter(event_id="evt_retry").count(), 1)

    def test_orphan_is_kept_for_retry_before_the_give_up_age(self):
        self.post_event(
            "checkout.session.completed",
            session_object("cs_nobody", "ORD-DOESNOTEXIST", 1999),
            "evt_orphan",
        )
        jobs_service.reconcile_stuck_webhooks()
        row = WebhookEvent.objects.get(event_id="evt_orphan")
        self.assertIsNone(row.processed_at)
        self.assertEqual(row.status, WebhookEvent.Status.ORPHANED)
        self.assertEqual(self.alerts().count(), 0)

    def test_orphan_is_given_up_with_an_alert_after_48_hours(self):
        self.post_event(
            "checkout.session.completed",
            session_object("cs_nobody", "ORD-DOESNOTEXIST", 1999),
            "evt_orphan",
        )
        WebhookEvent.objects.filter(event_id="evt_orphan").update(
            created_at=timezone.now() - timedelta(hours=49)
        )
        jobs_service.reconcile_stuck_webhooks()
        row = WebhookEvent.objects.get(event_id="evt_orphan")
        self.assertIsNotNone(row.processed_at)
        self.assertEqual(row.status, WebhookEvent.Status.ORPHANED)
        self.assertEqual(self.alerts().count(), 1)
        jobs_service.reconcile_stuck_webhooks()
        self.assertEqual(self.alerts().count(), 1)


class PayloadPurgeTests(ReconciliationTestCase):
    def _event(self, event_id, age_days, processed):
        return WebhookEvent.objects.create(
            provider="stripe",
            event_id=event_id,
            event_type="payment_succeeded",
            payload={"order_reference": "ORD-X"},
            processed_at=timezone.now() if processed else None,
            created_at=timezone.now() - timedelta(days=age_days),
        )

    def test_old_processed_payloads_are_emptied_and_the_rest_kept(self):
        old_processed = self._event("old", 100, True)
        recent = self._event("recent", 10, True)
        old_unprocessed = self._event("stuck", 100, False)
        WebhookEvent.objects.filter(pk=old_processed.pk).update(
            created_at=timezone.now() - timedelta(days=100)
        )

        purged = jobs_service.purge_old_webhook_payloads()

        self.assertEqual(purged, 1)
        old_processed.refresh_from_db()
        recent.refresh_from_db()
        old_unprocessed.refresh_from_db()
        self.assertEqual(old_processed.payload, {})
        self.assertEqual(recent.payload, {"order_reference": "ORD-X"})
        self.assertEqual(old_unprocessed.payload, {"order_reference": "ORD-X"})
        self.assertEqual(jobs_service.purge_old_webhook_payloads(), 0)
