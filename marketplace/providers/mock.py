import hashlib
import hmac
import json
import secrets
import time
import uuid

from django.conf import settings
from django.urls import reverse

from .base import (
    PaymentIntent,
    ProviderPaymentState,
    RefundResult,
    ReturnResult,
    VerifiedEvent,
    WebhookVerificationError,
)

_PROCESS_SECRET = secrets.token_hex(32)


def _secret():
    return settings.MARKETPLACE_MOCK_WEBHOOK_SECRET or _PROCESS_SECRET


def sign_payload(raw_body):
    return hmac.new(_secret().encode(), raw_body, hashlib.sha256).hexdigest()


def build_signed_event(event_type, order, amount_minor, currency, event_id=None):
    payload = {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "order_reference": order.reference,
        "provider_payment_id": f"mock_pay_{order.reference}",
        "amount_minor": amount_minor,
        "currency": currency,
        "timestamp": int(time.time()),
    }
    raw_body = json.dumps(payload).encode()
    signature = sign_payload(raw_body)
    return raw_body, signature


class MockProvider:
    slug = "mock"
    display_name = "Mock payment (development)"
    min_amount_minor = 0
    min_session_minutes = 0
    requires_capture = False

    def is_configured(self):
        return True

    def create_payment(self, order, payment, urls):
        provider_payment_id = f"mock_pay_{order.reference}"
        if payment.attempt > 1:
            provider_payment_id = f"{provider_payment_id}_{payment.attempt}"
        redirect_url = reverse("marketplace:mock_checkout", args=[order.reference])
        return PaymentIntent(
            provider_payment_id=provider_payment_id,
            redirect_url=redirect_url,
            client_secret=payment.idempotency_key,
        )

    def verify_webhook(self, raw_body, headers):
        signature = headers.get("X-Mock-Signature", "")
        expected = sign_payload(raw_body)
        if not signature or not hmac.compare_digest(signature, expected):
            raise WebhookVerificationError("Invalid mock webhook signature.")

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebhookVerificationError("Malformed mock webhook payload.") from exc

        timestamp = payload.get("timestamp")
        if timestamp is not None and abs(time.time() - timestamp) > 300:
            raise WebhookVerificationError("Mock webhook timestamp outside tolerance.")

        return self.normalize_event(payload)

    def normalize_event(self, stored_payload):
        try:
            return VerifiedEvent(
                event_id=stored_payload["event_id"],
                event_type=stored_payload["event_type"],
                order_reference=stored_payload["order_reference"],
                provider_payment_id=stored_payload["provider_payment_id"],
                amount_minor=stored_payload["amount_minor"],
                currency=stored_payload["currency"],
                raw=stored_payload,
                provider=self.slug,
                provider_refund_id=stored_payload.get("provider_refund_id", ""),
                refund_status=stored_payload.get("refund_status", ""),
                detail=stored_payload.get("detail", {}),
            )
        except KeyError as exc:
            raise WebhookVerificationError(f"Mock webhook payload missing {exc}.") from exc

    def handle_return(self, order, payment, params):
        return ReturnResult(status="unknown")

    def fetch_payment_state(self, payment):
        return ProviderPaymentState(status="unknown")

    def cancel_payment(self, payment):
        return None

    def capture_payment(self, payment):
        return ProviderPaymentState(status="unknown")

    def refund(self, payment, amount_minor, idempotency_key, refund_id=""):
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]
        return RefundResult(provider_refund_id=f"mock_refund_{digest}", status="completed")

    def fetch_refund(self, payment, provider_refund_id):
        return RefundResult(provider_refund_id=provider_refund_id, status="completed")
