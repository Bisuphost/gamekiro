from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from marketplace.models import BuyerVerification
from marketplace.services import ratelimit as ratelimit_service
from marketplace.services import verification as verification_service

from .decorators import require_marketplace_enabled

RESEND_LIMIT = 3
RESEND_WINDOW_SECONDS = 600


@login_required
@require_marketplace_enabled
def verify_email(request):
    if verification_service.is_verified(request.user):
        return redirect("marketplace:checkout")
    verification, _ = BuyerVerification.objects.get_or_create(user=request.user)
    if verification.last_sent_at is None:
        try:
            verification_service.send_verification_email(request.user)
        except verification_service.SendSuppressed:
            pass
    return render(request, "marketplace/verify_email.html", {})


@login_required
@require_marketplace_enabled
@require_POST
def verify_email_resend(request):
    if verification_service.is_verified(request.user):
        return redirect("marketplace:checkout")
    if ratelimit_service.hit(
        "verify_email_resend", request.user.pk, RESEND_LIMIT, RESEND_WINDOW_SECONDS
    ):
        try:
            verification_service.send_verification_email(request.user)
            messages.success(request, "Verification email sent — check your inbox.")
        except verification_service.SendSuppressed:
            messages.error(request, "Too many verification emails sent to this address recently.")
    else:
        messages.error(request, "You've requested this too many times. Please wait a bit.")
    return redirect("marketplace:verify_email")


@login_required
@require_marketplace_enabled
def verify_email_confirm(request, token):
    try:
        verification_service.verify_token(token, expected_user_id=request.user.pk)
    except verification_service.InvalidVerificationToken:
        messages.error(request, "That verification link is invalid or has expired.")
        return redirect("marketplace:verify_email")

    messages.success(request, "Your email is verified — you're all set to buy.")
    return redirect("marketplace:checkout")
