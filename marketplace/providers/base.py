from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol


class WebhookVerificationError(Exception):
    pass


class WebhookVerificationUnavailable(WebhookVerificationError):
    pass


class ProviderError(Exception):
    def __init__(self, message, code="", definitive=False, http_status=None, issues=()):
        super().__init__(message)
        self.code = code
        self.definitive = definitive
        self.http_status = http_status
        self.issues = tuple(issues)


@dataclass
class PaymentUrls:
    success_url: str
    cancel_url: str


@dataclass
class PaymentIntent:
    provider_payment_id: str
    redirect_url: str
    client_secret: str | None = None
    provider_reference: str = ""
    expires_at: datetime | None = None


@dataclass
class VerifiedEvent:
    event_id: str
    event_type: str
    order_reference: str
    provider_payment_id: str
    amount_minor: int | None
    currency: str
    raw: dict = field(default_factory=dict)
    provider_reference: str = ""
    provider: str = ""
    provider_refund_id: str = ""
    refund_status: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class ReturnResult:
    status: str
    event: VerifiedEvent | None = None


@dataclass
class ProviderPaymentState:
    status: str
    event: VerifiedEvent | None = None


@dataclass
class RefundResult:
    provider_refund_id: str
    status: str
    failure_reason: str = ""


class PaymentProvider(Protocol):
    slug: str
    display_name: str
    min_amount_minor: int
    min_session_minutes: int
    requires_capture: bool

    def is_configured(self) -> bool: ...

    def create_payment(self, order, payment, urls: PaymentUrls) -> PaymentIntent: ...

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> VerifiedEvent: ...

    def normalize_event(self, stored_payload: dict) -> VerifiedEvent: ...

    def handle_return(self, order, payment, params: Mapping[str, str]) -> ReturnResult: ...

    def fetch_payment_state(self, payment) -> ProviderPaymentState: ...

    def cancel_payment(self, payment) -> None: ...

    def capture_payment(self, payment) -> ProviderPaymentState: ...

    def refund(self, payment, amount_minor, idempotency_key, refund_id="") -> RefundResult: ...

    def fetch_refund(self, payment, provider_refund_id) -> RefundResult: ...
