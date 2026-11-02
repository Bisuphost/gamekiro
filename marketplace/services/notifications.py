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


def queue_invoice_email(order):
    invoice_path = reverse("marketplace:order_invoice", args=[order.reference])
    invoice_url = f"{settings.SITE_URL}{invoice_path}"
    lines = "\n".join(
        f"  {item.product_title} ({item.platform_name or 'Global'}) "
        f"- {_format_money(item.unit_price_minor, order.currency)}"
        for item in order.items.all()
    )
    body = (
        f"Hi {order.user.get_full_name() or order.user.username},\n\n"
        f"Thanks for your purchase. Here's your receipt for order {order.reference}.\n\n"
        f"{lines}\n\n"
        f"Subtotal: {_format_money(order.subtotal_minor, order.currency)}\n"
        f"Discount: -{_format_money(order.discount_minor, order.currency)}\n"
        f"Total paid: {_format_money(order.total_minor, order.currency)}\n\n"
        f"View your invoice: {invoice_url}\n\n"
        "Thanks for shopping with GameKiro."
    )
    OutboundEmail.objects.create(
        to_email=order.user.email,
        subject=f"Your GameKiro invoice for order {order.reference}",
        body_text=body,
        related_order=order,
    )


def _format_money(value_minor, currency):
    symbols = {"USD": "$", "EUR": "€", "GBP": "£"}
    amount = value_minor / 100
    symbol = symbols.get(currency)
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {currency}"


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
