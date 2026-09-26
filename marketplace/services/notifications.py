from django.conf import settings
from django.urls import reverse

from marketplace.models import OutboundEmail


def queue_order_confirmation_email(order):
    order_url = f"{settings.SITE_URL}{reverse('marketplace:order_detail', args=[order.reference])}"
    body = (
        f"Hi {order.user.get_full_name() or order.user.username},\n\n"
        f"Your GameKiro order {order.reference} has been fulfilled. Your keys are ready "
        "in your library.\n\n"
        f"View your order: {order_url}\n\n"
        "Thanks for shopping with GameKiro."
    )
    OutboundEmail.objects.create(
        to_email=order.user.email,
        subject=f"Your GameKiro order {order.reference} is ready",
        body_text=body,
        related_order=order,
    )
    _notify_in_app(order)


def _notify_in_app(order):
    from notifications.models import Notification
    from notifications.services import notify

    notify(
        recipient=order.user,
        actor=None,
        verb=Notification.Verb.ORDER_FULFILLED,
        target=order,
    )


def notify_order_needs_review(order):
    from notifications.models import Notification
    from notifications.services import notify

    notify(
        recipient=order.user,
        actor=None,
        verb=Notification.Verb.ORDER_NEEDS_REVIEW,
        target=order,
    )


def queue_staff_alert(subject, body, order=None):
    OutboundEmail.objects.create(
        to_email=settings.MARKETPLACE_SUPPORT_EMAIL,
        subject=f"[GameKiro payments] {subject}",
        body_text=body,
        related_order=order,
    )
