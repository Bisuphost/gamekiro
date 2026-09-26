import json
from datetime import timedelta

import stripe
from django.conf import settings

from .base import (
    PaymentIntent,
    ProviderError,
    ProviderPaymentState,
    RefundResult,
    ReturnResult,
    VerifiedEvent,
    WebhookVerificationError,
)

SESSION_LIFETIME_MINUTES = 31
SIGNATURE_TOLERANCE_SECONDS = 300
CONNECT_TIMEOUT_SECONDS = 3.05
READ_TIMEOUT_SECONDS = 10
NETWORK_RETRIES = 2
TWO_DECIMAL_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD"}

REFUND_EVENT_TYPES = {"refund.created", "refund.updated", "refund.failed"}
DISPUTE_EVENT_TYPES = {
    "charge.dispute.created": "dispute_created",
    "charge.dispute.updated": "dispute_updated",
    "charge.dispute.funds_withdrawn": "dispute_updated",
    "charge.dispute.funds_reinstated": "dispute_updated",
    "charge.dispute.closed": "dispute_closed",
}

SESSION_EVENT_TYPES = {
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
}

_clients = {}


def _client():
    key = settings.STRIPE_SECRET_KEY
    if not key:
        raise ProviderError("Stripe is not configured.", code="not_configured", definitive=True)
    cache_key = (key, settings.STRIPE_API_VERSION)
    if cache_key not in _clients:
        _clients[cache_key] = stripe.StripeClient(
            key,
            max_network_retries=NETWORK_RETRIES,
            stripe_version=settings.STRIPE_API_VERSION or None,
            http_client=stripe.RequestsClient(
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
            ),
        )
    return _clients[cache_key]


def _provider_error(exc):
    definitive = isinstance(
        exc,
        (
            stripe.InvalidRequestError,
            stripe.AuthenticationError,
            stripe.PermissionError,
            stripe.IdempotencyError,
        ),
    )
    code = getattr(exc, "code", None) or type(exc).__name__
    return ProviderError(
        f"Stripe request failed ({code}).", code=str(code)[:64], definitive=definitive
    )


def _value(obj, key, default=""):
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


class StripeProvider:
    slug = "stripe"
    display_name = "Card or wallet (Stripe)"
    min_amount_minor = 50
    min_session_minutes = SESSION_LIFETIME_MINUTES
    requires_capture = False

    def __init__(self, client=None):
        self._injected_client = client

    @property
    def client(self):
        return self._injected_client or _client()

    def is_configured(self):
        return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRETS)

    def create_payment(self, order, payment, urls):
        currency = order.currency.upper()
        if currency not in TWO_DECIMAL_CURRENCIES:
            raise ProviderError(
                f"Currency {currency} is not supported.", code="currency", definitive=True
            )
        item_count = order.items.count()
        metadata = {"order_reference": order.reference, "payment_attempt": str(payment.attempt)}
        params = {
            "mode": "payment",
            "client_reference_id": order.reference,
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": currency.lower(),
                        "unit_amount": order.total_minor,
                        "product_data": {
                            "name": f"GameKiro order {order.reference}",
                            "description": f"{item_count} digital game key(s)",
                        },
                    },
                }
            ],
            "payment_method_types": ["card"],
            "success_url": f"{urls.success_url}?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{urls.cancel_url}?payment=cancelled",
            "expires_at": int(
                (payment.created_at + timedelta(minutes=SESSION_LIFETIME_MINUTES)).timestamp()
            ),
            "metadata": metadata,
            "payment_intent_data": {
                "metadata": metadata,
                "description": f"GameKiro order {order.reference}",
            },
        }
        email = getattr(order.user, "email", "")
        if email:
            params["customer_email"] = email

        try:
            session = self.client.v1.checkout.sessions.create(
                params=params, options={"idempotency_key": f"{payment.idempotency_key}:create"}
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc

        if not _value(session, "url") or not _value(session, "id"):
            raise ProviderError("Stripe returned no checkout URL.", code="no_url", definitive=True)
        return PaymentIntent(
            provider_payment_id=session["id"],
            redirect_url=session["url"],
            expires_at=payment.created_at + timedelta(minutes=SESSION_LIFETIME_MINUTES),
        )

    def verify_webhook(self, raw_body, headers):
        secrets = settings.STRIPE_WEBHOOK_SECRETS
        if not secrets:
            raise WebhookVerificationError("Stripe webhook secret is not configured.")
        signature = headers.get("Stripe-Signature")
        try:
            verified = any(_signature_matches(raw_body, signature, secret) for secret in secrets)
        except UnicodeDecodeError as exc:
            raise WebhookVerificationError("Malformed Stripe webhook payload.") from exc
        if not verified:
            raise WebhookVerificationError("Invalid Stripe webhook signature.")
        try:
            payload = json.loads(raw_body)
            return self._event_from_payload(payload)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WebhookVerificationError("Malformed Stripe webhook payload.") from exc

    def normalize_event(self, stored_payload):
        try:
            return VerifiedEvent(
                event_id=stored_payload["event_id"],
                event_type=stored_payload["event_type"],
                order_reference=stored_payload.get("order_reference", ""),
                provider_payment_id=stored_payload.get("provider_payment_id", ""),
                provider_reference=stored_payload.get("provider_reference", ""),
                amount_minor=stored_payload.get("amount_minor"),
                currency=stored_payload.get("currency", ""),
                raw=stored_payload,
                provider=self.slug,
                provider_refund_id=stored_payload.get("provider_refund_id", ""),
                refund_status=stored_payload.get("refund_status", ""),
                detail=stored_payload.get("detail", {}),
            )
        except KeyError as exc:
            raise WebhookVerificationError(f"Stored Stripe event missing {exc}.") from exc

    def _event_from_payload(self, payload):
        stripe_type = payload["type"]
        event_id = payload["id"]
        obj = payload["data"]["object"]
        if stripe_type in REFUND_EVENT_TYPES:
            return self._refund_event(event_id, stripe_type, obj)
        if stripe_type in DISPUTE_EVENT_TYPES:
            return self._dispute_event(event_id, stripe_type, obj)
        if stripe_type not in SESSION_EVENT_TYPES or _value(obj, "mode") != "payment":
            return self.normalize_event(
                {"event_id": event_id, "event_type": stripe_type, "stripe_type": stripe_type}
            )
        return self._session_event(event_id, stripe_type, obj)

    def _refund_event(self, event_id, stripe_type, refund):
        metadata = _value(refund, "metadata", {})
        payment_intent = _value(refund, "payment_intent")
        return self.normalize_event(
            {
                "event_id": event_id,
                "event_type": "refund_updated",
                "stripe_type": stripe_type,
                "order_reference": _value(metadata, "order_reference"),
                "provider_reference": payment_intent if isinstance(payment_intent, str) else "",
                "amount_minor": _value(refund, "amount", None),
                "currency": str(_value(refund, "currency")).upper(),
                "provider_refund_id": refund["id"],
                "refund_status": _value(refund, "status"),
                "detail": {
                    "refund_id": _value(metadata, "refund_id"),
                    "failure_reason": _value(refund, "failure_reason"),
                },
            }
        )

    def _dispute_event(self, event_id, stripe_type, dispute):
        payment_intent = _value(dispute, "payment_intent")
        evidence = _value(dispute, "evidence_details", {})
        status = _value(dispute, "status")
        internal = DISPUTE_EVENT_TYPES[stripe_type]
        return self.normalize_event(
            {
                "event_id": event_id,
                "event_type": internal,
                "stripe_type": stripe_type,
                "provider_reference": payment_intent if isinstance(payment_intent, str) else "",
                "amount_minor": _value(dispute, "amount", None),
                "currency": str(_value(dispute, "currency")).upper(),
                "detail": {
                    "dispute_id": dispute["id"],
                    "status": status,
                    "reason": _value(dispute, "reason"),
                    "due_by": _value(evidence, "due_by", None),
                    "outcome": status if internal == "dispute_closed" else "",
                },
            }
        )

    def _session_event(self, event_id, stripe_type, session):
        payment_status = _value(session, "payment_status")
        if stripe_type in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        }:
            event_type = "payment_succeeded" if payment_status == "paid" else "payment_pending"
        elif stripe_type == "checkout.session.async_payment_failed":
            event_type = "payment_failed"
        else:
            event_type = "attempt_expired"
        metadata = _value(session, "metadata", {})
        payment_intent = _value(session, "payment_intent")
        return self.normalize_event(
            {
                "event_id": event_id,
                "event_type": event_type,
                "stripe_type": stripe_type,
                "order_reference": _value(session, "client_reference_id")
                or _value(metadata, "order_reference"),
                "provider_payment_id": session["id"],
                "provider_reference": payment_intent if isinstance(payment_intent, str) else "",
                "amount_minor": _value(session, "amount_total", None),
                "currency": str(_value(session, "currency")).upper(),
                "payment_status": payment_status,
            }
        )

    def fetch_payment_state(self, payment):
        if not payment.provider_payment_id:
            return ProviderPaymentState(status="unknown")
        try:
            session = self.client.v1.checkout.sessions.retrieve(payment.provider_payment_id)
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc

        status = _value(session, "status")
        payment_status = _value(session, "payment_status")
        event_id = f"pull:{session['id']}:{status}:{payment_status}"
        if status == "complete" and payment_status == "paid":
            event = self._session_event(event_id, "checkout.session.completed", session)
            return ProviderPaymentState(status="paid", event=event)
        if status == "expired":
            event = self._session_event(event_id, "checkout.session.expired", session)
            return ProviderPaymentState(status="expired", event=event)
        if status == "complete":
            return ProviderPaymentState(status="pending")
        return ProviderPaymentState(status="open")

    def handle_return(self, order, payment, params):
        session_id = params.get("session_id", "")
        if not session_id or session_id != payment.provider_payment_id:
            return ReturnResult(status="ignored")
        state = self.fetch_payment_state(payment)
        return ReturnResult(status=state.status, event=state.event)

    def cancel_payment(self, payment):
        if not payment.provider_payment_id:
            return None
        try:
            self.client.v1.checkout.sessions.expire(payment.provider_payment_id)
            return None
        except stripe.InvalidRequestError:
            pass
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc

        try:
            session = self.client.v1.checkout.sessions.retrieve(payment.provider_payment_id)
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        if _value(session, "status") != "expired":
            raise ProviderError(
                "The previous checkout session cannot be expired.",
                code="session_not_expirable",
                definitive=True,
            )
        return None

    def capture_payment(self, payment):
        return ProviderPaymentState(status="unknown")

    def refund(self, payment, amount_minor, idempotency_key, refund_id=""):
        if not payment.provider_reference:
            raise ProviderError(
                "No Stripe PaymentIntent is recorded for this payment.",
                code="no_payment_intent",
                definitive=True,
            )
        metadata = {"order_reference": payment.order.reference}
        if refund_id:
            metadata["refund_id"] = str(refund_id)
        try:
            refund = self.client.v1.refunds.create(
                params={
                    "payment_intent": payment.provider_reference,
                    "amount": amount_minor,
                    "metadata": metadata,
                },
                options={"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        return RefundResult(
            provider_refund_id=refund["id"],
            status=_value(refund, "status"),
            failure_reason=_value(refund, "failure_reason"),
        )

    def fetch_refund(self, payment, provider_refund_id):
        try:
            refund = self.client.v1.refunds.retrieve(provider_refund_id)
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        return RefundResult(
            provider_refund_id=refund["id"],
            status=_value(refund, "status"),
            failure_reason=_value(refund, "failure_reason"),
        )


def _signature_matches(raw_body, signature, secret):
    try:
        return stripe.WebhookSignature.verify_header(
            raw_body, signature, secret, tolerance=SIGNATURE_TOLERANCE_SECONDS
        )
    except stripe.SignatureVerificationError:
        return False
