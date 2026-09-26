import json
import threading
import time
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

from .base import (
    PaymentIntent,
    ProviderError,
    ProviderPaymentState,
    RefundResult,
    ReturnResult,
    VerifiedEvent,
    WebhookVerificationError,
    WebhookVerificationUnavailable,
)

BASE_URLS = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}
CONNECT_TIMEOUT_SECONDS = 3.05
READ_TIMEOUT_SECONDS = 10
TOKEN_EXPIRY_MARGIN_SECONDS = 60
TWO_DECIMAL_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD"}
ESCALATED_DISPUTE_STAGES = {"CHARGEBACK", "PRE_ARBITRATION", "ARBITRATION"}
WEBHOOK_HEADERS = (
    "PAYPAL-AUTH-ALGO",
    "PAYPAL-CERT-URL",
    "PAYPAL-TRANSMISSION-ID",
    "PAYPAL-TRANSMISSION-SIG",
    "PAYPAL-TRANSMISSION-TIME",
)

_token_lock = threading.Lock()
_token_cache = {}


def minor_to_amount_string(amount_minor, currency):
    if currency.upper() not in TWO_DECIMAL_CURRENCIES:
        raise ProviderError(
            f"Currency {currency} is not supported.", code="currency", definitive=True
        )
    return f"{amount_minor // 100}.{amount_minor % 100:02d}"


def amount_string_to_minor(value, currency):
    if currency.upper() not in TWO_DECIMAL_CURRENCIES:
        raise ValueError(f"Unsupported currency {currency}")
    text = str(value)
    if "e" in text.lower():
        raise ValueError(f"Invalid amount {value!r}")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount {value!r}") from exc
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise ValueError(f"Invalid amount {value!r}")
    return int(amount * 100)


def _issues(body):
    details = body.get("details") if isinstance(body, dict) else None
    if not isinstance(details, list):
        return ()
    return tuple(d.get("issue", "") for d in details if isinstance(d, dict))


def _related_capture_id(resource):
    # The PAYMENT.CAPTURE.REFUNDED webhook's `resource` is the refund object
    # itself, not the capture — it has no `capture_id` field. The only way
    # back to the capture we actually stored on the Payment row is the "up"
    # link PayPal includes (e.g. .../v2/payments/captures/<id>).
    for link in resource.get("links", []) or []:
        if link.get("rel") == "up":
            return link.get("href", "").rstrip("/").rsplit("/", 1)[-1]
    return resource.get("id", "")


class PayPalProvider:
    slug = "paypal"
    display_name = "PayPal"
    min_amount_minor = 1
    min_session_minutes = 0
    requires_capture = True

    def __init__(self, session=None):
        self._session = session or requests.Session()

    def is_configured(self):
        return bool(
            settings.PAYPAL_CLIENT_ID
            and settings.PAYPAL_CLIENT_SECRET
            and settings.PAYPAL_WEBHOOK_ID
        )

    @property
    def base_url(self):
        return BASE_URLS.get(settings.PAYPAL_ENV, BASE_URLS["sandbox"])

    def _access_token(self, force=False):
        cache_key = (settings.PAYPAL_ENV, settings.PAYPAL_CLIENT_ID)
        with _token_lock:
            cached = _token_cache.get(cache_key)
            if cached and not force and cached[1] > time.monotonic():
                return cached[0]
            try:
                response = self._session.request(
                    "POST",
                    f"{self.base_url}/v1/oauth2/token",
                    auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
                    data={"grant_type": "client_credentials"},
                    headers={"Accept": "application/json"},
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise ProviderError("PayPal authentication unreachable.", code="network") from exc
            if response.status_code != 200:
                raise ProviderError(
                    "PayPal authentication failed.",
                    code="auth",
                    definitive=response.status_code in (400, 401, 403),
                    http_status=response.status_code,
                )
            try:
                payload = response.json()
                token = payload["access_token"]
                lifetime = int(payload.get("expires_in", 0))
            except (ValueError, KeyError, TypeError) as exc:
                raise ProviderError("PayPal returned an invalid token.", code="auth") from exc
            expires = time.monotonic() + max(lifetime - TOKEN_EXPIRY_MARGIN_SECONDS, 0)
            _token_cache[cache_key] = (token, expires)
            return token

    def _request(self, method, path, body=None, request_id=None, extra_headers=None, retries=1):
        data = None if body is None else json.dumps(body).encode()
        refreshed = False
        attempt = 0
        while True:
            headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": "application/json",
            }
            if data is not None:
                headers["Content-Type"] = "application/json"
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            if extra_headers:
                headers.update(extra_headers)
            try:
                response = self._session.request(
                    method,
                    f"{self.base_url}{path}",
                    data=data,
                    headers=headers,
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                if attempt < retries and (request_id or method == "GET"):
                    attempt += 1
                    continue
                raise ProviderError("PayPal is unreachable.", code="network") from exc

            status = response.status_code
            if status == 401 and not refreshed:
                refreshed = True
                self._access_token(force=True)
                continue
            if status >= 500 or status == 429:
                if attempt < retries and (request_id or method == "GET"):
                    attempt += 1
                    continue
            if 200 <= status < 300:
                try:
                    return response.json() if response.content else {}
                except ValueError as exc:
                    raise ProviderError(
                        "PayPal returned an unreadable response.", code="bad_response"
                    ) from exc
            try:
                error_body = response.json()
            except ValueError:
                error_body = {}
            issues = _issues(error_body)
            code = (issues[0] if issues else (error_body.get("name") or f"http_{status}"))[:64]
            raise ProviderError(
                f"PayPal request failed ({code}).",
                code=code,
                definitive=status in (400, 401, 403, 404, 409, 422),
                http_status=status,
                issues=issues,
            )

    def create_payment(self, order, payment, urls):
        currency = order.currency.upper()
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": order.reference,
                    "custom_id": order.reference,
                    "description": f"GameKiro order {order.reference}",
                    "amount": {
                        "currency_code": currency,
                        "value": minor_to_amount_string(order.total_minor, currency),
                    },
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "GameKiro",
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                        "return_url": urls.success_url,
                        "cancel_url": urls.cancel_url,
                    }
                }
            },
        }
        data = self._request(
            "POST", "/v2/checkout/orders", body, request_id=f"create:{payment.idempotency_key}"
        )
        order_id = data.get("id", "")
        approve_url = next(
            (
                link.get("href", "")
                for link in data.get("links", [])
                if link.get("rel") in ("payer-action", "approve")
            ),
            "",
        )
        if not order_id or not approve_url.startswith("https://"):
            raise ProviderError(
                "PayPal returned no approval link.", code="no_approval_link", definitive=True
            )
        return PaymentIntent(
            provider_payment_id=order_id,
            redirect_url=approve_url,
            expires_at=order.reservation_expires_at,
        )

    def _event(self, event_type, event_id, payment, capture):
        currency = (capture.get("amount") or {}).get("currency_code", "").upper()
        return VerifiedEvent(
            event_id=event_id,
            event_type=event_type,
            order_reference=payment.order.reference,
            provider_payment_id=payment.provider_payment_id,
            provider_reference=capture.get("id", ""),
            amount_minor=amount_string_to_minor(capture["amount"]["value"], currency),
            currency=currency,
            raw={
                "event_id": event_id,
                "event_type": event_type,
                "order_reference": payment.order.reference,
                "provider_payment_id": payment.provider_payment_id,
                "provider_reference": capture.get("id", ""),
                "amount_minor": amount_string_to_minor(capture["amount"]["value"], currency),
                "currency": currency,
            },
            provider=self.slug,
        )

    def _state_from_order(self, payment, data):
        status = data.get("status", "")
        units = data.get("purchase_units") or []
        captures = ((units[0].get("payments") or {}).get("captures") or []) if units else []

        if status == "COMPLETED":
            if len(captures) != 1:
                raise ProviderError(
                    "PayPal order has an unexpected number of captures.",
                    code="unexpected_captures",
                    definitive=True,
                )
            capture = captures[0]
            capture_status = capture.get("status", "")
            event_id = f"capture:{capture.get('id', '')}:{capture_status}"
            try:
                if capture_status == "COMPLETED":
                    event = self._event("payment_succeeded", event_id, payment, capture)
                    return ProviderPaymentState(status="paid", event=event)
                if capture_status in ("DECLINED", "FAILED"):
                    event = self._event("attempt_failed", event_id, payment, capture)
                    return ProviderPaymentState(status="failed", event=event)
            except (KeyError, ValueError) as exc:
                raise ProviderError(
                    "PayPal capture has an invalid amount.", code="bad_amount", definitive=True
                ) from exc
            return ProviderPaymentState(status="pending")
        if status == "APPROVED":
            return ProviderPaymentState(status="approved")
        if status == "VOIDED":
            event_id = f"order:{payment.provider_payment_id}:voided"
            event = self.normalize_event(
                {
                    "event_id": event_id,
                    "event_type": "attempt_expired",
                    "order_reference": payment.order.reference,
                    "provider_payment_id": payment.provider_payment_id,
                }
            )
            return ProviderPaymentState(status="expired", event=event)
        return ProviderPaymentState(status="open")

    def fetch_payment_state(self, payment):
        if not payment.provider_payment_id:
            return ProviderPaymentState(status="unknown")
        data = self._request("GET", f"/v2/checkout/orders/{payment.provider_payment_id}")
        return self._state_from_order(payment, data)

    def handle_return(self, order, payment, params):
        token = params.get("token", "")
        if not token or token != payment.provider_payment_id:
            return ReturnResult(status="ignored")
        state = self.fetch_payment_state(payment)
        return ReturnResult(status=state.status, event=state.event)

    def capture_payment(self, payment):
        try:
            data = self._request(
                "POST",
                f"/v2/checkout/orders/{payment.provider_payment_id}/capture",
                {},
                request_id=f"capture:{payment.idempotency_key}",
                extra_headers={"Prefer": "return=representation"},
            )
        except ProviderError as exc:
            if not exc.definitive or "PREVIOUS_REQUEST_IN_PROGRESS" in exc.issues:
                raise
            state = self.fetch_payment_state(payment)
            if state.status in ("paid", "pending", "failed"):
                return state
            failed = self.normalize_event(
                {
                    "event_id": f"capture-rejected:{payment.idempotency_key}",
                    "event_type": "attempt_failed",
                    "order_reference": payment.order.reference,
                    "provider_payment_id": payment.provider_payment_id,
                }
            )
            return ProviderPaymentState(status="failed", event=failed)
        return self._state_from_order(payment, data)

    def cancel_payment(self, payment):
        return None

    def verify_webhook(self, raw_body, headers):
        webhook_id = settings.PAYPAL_WEBHOOK_ID
        if not webhook_id:
            raise WebhookVerificationError("PayPal webhook ID is not configured.")
        values = {name: headers.get(name) for name in WEBHOOK_HEADERS}
        if not all(values.values()):
            raise WebhookVerificationError("Missing PayPal signature headers.")
        try:
            text = raw_body.decode("utf-8")
            event = json.loads(text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise WebhookVerificationError("Malformed PayPal webhook payload.") from exc
        if not isinstance(event, dict) or not event.get("id") or not event.get("event_type"):
            raise WebhookVerificationError("Malformed PayPal webhook payload.")

        request_body = (
            b'{"auth_algo":'
            + json.dumps(values["PAYPAL-AUTH-ALGO"]).encode()
            + b',"cert_url":'
            + json.dumps(values["PAYPAL-CERT-URL"]).encode()
            + b',"transmission_id":'
            + json.dumps(values["PAYPAL-TRANSMISSION-ID"]).encode()
            + b',"transmission_sig":'
            + json.dumps(values["PAYPAL-TRANSMISSION-SIG"]).encode()
            + b',"transmission_time":'
            + json.dumps(values["PAYPAL-TRANSMISSION-TIME"]).encode()
            + b',"webhook_id":'
            + json.dumps(webhook_id).encode()
            + b',"webhook_event":'
            + raw_body.strip()
            + b"}"
        )
        try:
            result = self._verify_request(request_body)
        except ProviderError as exc:
            if exc.definitive and exc.http_status in (400, 422):
                raise WebhookVerificationError("PayPal could not verify the signature.") from exc
            raise WebhookVerificationUnavailable("PayPal verification is unavailable.") from exc
        if result.get("verification_status") != "SUCCESS":
            raise WebhookVerificationError("Invalid PayPal webhook signature.")
        return self._event_from_payload(event)

    def _verify_request(self, request_body):
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = self._session.request(
                "POST",
                f"{self.base_url}/v1/notifications/verify-webhook-signature",
                data=request_body,
                headers=headers,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ProviderError("PayPal is unreachable.", code="network") from exc
        if response.status_code != 200:
            raise ProviderError(
                "PayPal verification failed.",
                code=f"http_{response.status_code}",
                definitive=response.status_code in (400, 422),
                http_status=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                "PayPal returned an unreadable response.", code="bad_response"
            ) from exc

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
            raise WebhookVerificationError(f"Stored PayPal event missing {exc}.") from exc

    def _event_from_payload(self, payload):
        paypal_type = payload["event_type"]
        event_id = payload["id"]
        resource = payload.get("resource") or {}
        base = {"event_id": event_id, "event_type": paypal_type, "paypal_type": paypal_type}

        if paypal_type == "CHECKOUT.ORDER.APPROVED":
            units = resource.get("purchase_units") or [{}]
            return self.normalize_event(
                {
                    **base,
                    "event_type": "buyer_approved",
                    "order_reference": units[0].get("custom_id", ""),
                    "provider_payment_id": resource.get("id", ""),
                }
            )

        capture_types = {
            "PAYMENT.CAPTURE.COMPLETED": "payment_succeeded",
            "PAYMENT.CAPTURE.PENDING": "payment_pending",
            "PAYMENT.CAPTURE.DECLINED": "attempt_failed",
        }
        if paypal_type in capture_types:
            amount = resource.get("amount") or {}
            currency = str(amount.get("currency_code", "")).upper()
            try:
                amount_minor = amount_string_to_minor(amount.get("value"), currency)
            except ValueError as exc:
                raise WebhookVerificationError("Invalid capture amount.") from exc
            related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
            internal = capture_types[paypal_type]
            if internal == "payment_succeeded" and resource.get("status") != "COMPLETED":
                internal = "payment_pending"
            return self.normalize_event(
                {
                    **base,
                    "event_type": internal,
                    "order_reference": resource.get("custom_id", ""),
                    "provider_payment_id": related.get("order_id", ""),
                    "provider_reference": resource.get("id", ""),
                    "amount_minor": amount_minor,
                    "currency": currency,
                }
            )
        if paypal_type.startswith("CUSTOMER.DISPUTE."):
            return self._dispute_event(base, paypal_type, resource)

        if paypal_type == "PAYMENT.CAPTURE.REVERSED":
            return self.normalize_event(
                {
                    **base,
                    "event_type": "dispute_created",
                    "order_reference": resource.get("custom_id", ""),
                    "provider_reference": _related_capture_id(resource),
                    "detail": {"reversal": True},
                }
            )
        if paypal_type == "PAYMENT.CAPTURE.REFUNDED":
            amount = resource.get("amount") or {}
            currency = str(amount.get("currency_code", "")).upper()
            try:
                amount_minor = (
                    amount_string_to_minor(amount.get("value"), currency) if amount else None
                )
            except ValueError:
                amount_minor = None
            return self.normalize_event(
                {
                    **base,
                    "event_type": "refund_updated",
                    "order_reference": resource.get("custom_id", ""),
                    "provider_reference": _related_capture_id(resource),
                    "provider_refund_id": resource.get("id", ""),
                    "refund_status": resource.get("status", ""),
                    "amount_minor": amount_minor,
                    "currency": currency,
                }
            )
        return self.normalize_event(base)

    def _dispute_event(self, base, paypal_type, resource):
        transactions = resource.get("disputed_transactions") or [{}]
        amount = resource.get("dispute_amount") or {}
        currency = str(amount.get("currency_code", "")).upper()
        try:
            amount_minor = amount_string_to_minor(amount.get("value"), currency) if amount else None
        except ValueError:
            amount_minor = None
        stage = resource.get("dispute_life_cycle_stage", "")
        escalated = stage in ESCALATED_DISPUTE_STAGES
        if paypal_type.endswith(".RESOLVED"):
            internal = "dispute_closed"
        elif escalated:
            internal = "dispute_created"
        else:
            internal = "dispute_updated"
        return self.normalize_event(
            {
                **base,
                "event_type": internal,
                "provider_reference": transactions[0].get("seller_transaction_id", ""),
                "amount_minor": amount_minor,
                "currency": currency,
                "detail": {
                    "dispute_id": resource.get("dispute_id", ""),
                    "status": resource.get("status", ""),
                    "reason": resource.get("reason", ""),
                    "stage": stage,
                    "due_by": resource.get("seller_response_due_date"),
                    "outcome": (resource.get("dispute_outcome") or {}).get("outcome_code", ""),
                },
            }
        )

    def refund(self, payment, amount_minor, idempotency_key, refund_id=""):
        if not payment.provider_reference:
            raise ProviderError(
                "No PayPal capture is recorded for this payment.",
                code="no_capture",
                definitive=True,
            )
        currency = payment.currency.upper()
        data = self._request(
            "POST",
            f"/v2/payments/captures/{payment.provider_reference}/refund",
            {
                "amount": {
                    "currency_code": currency,
                    "value": minor_to_amount_string(amount_minor, currency),
                },
                "custom_id": payment.order.reference,
            },
            request_id=idempotency_key,
        )
        return RefundResult(
            provider_refund_id=data.get("id", ""),
            status=data.get("status", ""),
            failure_reason=(data.get("status_details") or {}).get("reason", ""),
        )

    def fetch_refund(self, payment, provider_refund_id):
        data = self._request("GET", f"/v2/payments/refunds/{provider_refund_id}")
        return RefundResult(
            provider_refund_id=data.get("id", provider_refund_id),
            status=data.get("status", ""),
            failure_reason=(data.get("status_details") or {}).get("reason", ""),
        )
