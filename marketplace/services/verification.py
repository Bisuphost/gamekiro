from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from marketplace.models import BuyerVerification, OutboundEmail

from . import ratelimit as ratelimit_service

SALT = "marketplace-email-verify"
TOKEN_MAX_AGE_SECONDS = 24 * 60 * 60
EMAIL_SEND_ACTION = "verify_email_send"
EMAIL_SEND_LIMIT = 5
EMAIL_SEND_WINDOW_SECONDS = 60 * 60


class SendSuppressed(Exception):
    pass


class InvalidVerificationToken(Exception):
    pass


def required():
    return bool(settings.MARKETPLACE_REQUIRE_VERIFIED_EMAIL)


def is_verified(user):
    if not required():
        return True
    if not user.email:
        return False
    verification = getattr(user, "marketplace_verification", None)
    return bool(verification and verification.is_verified_for(user.email))


def make_token(user):
    return signing.dumps({"user_id": user.pk, "email": user.email}, salt=SALT, compress=True)


def verify_token(token, expected_user_id):
    try:
        payload = signing.loads(token, salt=SALT, max_age=TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature as exc:
        raise InvalidVerificationToken("This verification link is invalid.") from exc

    user_id = payload.get("user_id")
    email = payload.get("email")
    if not user_id or not email:
        raise InvalidVerificationToken("This verification link is invalid.")
    if user_id != expected_user_id:
        raise InvalidVerificationToken("This verification link belongs to a different account.")

    now = timezone.now()
    verification, _ = BuyerVerification.objects.get_or_create(user_id=user_id)
    verification.email = email
    verification.verified_at = now
    verification.save(update_fields=["email", "verified_at", "updated_at"])
    return verification


def send_verification_email(user):
    if not user.email:
        return
    # Django doesn't require unique emails at signup, so many accounts can share
    # one inbox. The per-user "resend" limit alone wouldn't stop someone from
    # mail-bombing a victim's address through several throwaway accounts; cap the
    # total volume per destination address too, independent of who's asking.
    if not ratelimit_service.hit(
        EMAIL_SEND_ACTION, user.email.lower(), EMAIL_SEND_LIMIT, EMAIL_SEND_WINDOW_SECONDS
    ):
        raise SendSuppressed("Too many verification emails sent to this address recently.")

    verification, _ = BuyerVerification.objects.get_or_create(user_id=user.pk)
    now = timezone.now()
    BuyerVerification.objects.filter(pk=verification.pk).update(last_sent_at=now, updated_at=now)

    token = make_token(user)
    path = reverse("marketplace:verify_email_confirm", args=[token])
    verify_url = f"{settings.SITE_URL.rstrip('/')}{path}"
    body = (
        f"Hi {user.get_full_name() or user.username},\n\n"
        "Confirm your email to buy on the GameKiro store:\n\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours. If you didn't request this, you can ignore it.\n\n"
        "Thanks,\nGameKiro"
    )
    OutboundEmail.objects.create(
        to_email=user.email,
        subject="Confirm your email to buy on GameKiro",
        body_text=body,
    )
