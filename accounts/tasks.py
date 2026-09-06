import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def send_welcome_email(self, user_id):
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("Welcome email skipped: missing user %s", user_id)
        return

    profile_url = f"{settings.SITE_URL}/accounts/setup/"
    try:
        send_mail(
            subject="Welcome to GameKiro",
            message=(
                f"Hi {user.get_full_name() or user.username},\n\n"
                "Welcome to GameKiro! We’re glad you joined the community. "
                "Take a minute to finish your profile so other players can get to know you.\n\n"
                f"Profile setup: {profile_url}\n\n"
                "Thanks for joining the GameKiro community."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Welcome email failed for user %s", user_id)
        raise
