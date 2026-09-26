from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from marketplace.forms import ImportKeysForm
from marketplace.models import (
    GameKey,
    InventoryImportBatch,
    KeyAccessLog,
    MarketplaceSettings,
    Order,
    Product,
    Refund,
)
from marketplace.services import audit as audit_service
from marketplace.services import flags as flags_service
from marketplace.services import inventory as inventory_service
from marketplace.services import keys as keys_service
from marketplace.services import refunds as refunds_service

from .utils import client_ip

PAID_LIKE_STATUSES = [
    Order.Status.PAID,
    Order.Status.PAID_LATE,
    Order.Status.PARTIALLY_FULFILLED,
    Order.Status.FULFILLED,
]


@login_required
@permission_required("marketplace.view_dashboard", raise_exception=True)
def dashboard(request):
    today = timezone.now().date()
    revenue_minor = (
        Order.objects.filter(status__in=PAID_LIKE_STATUSES).aggregate(total=Sum("total_minor"))[
            "total"
        ]
        or 0
    )
    stats = {
        "orders_today": Order.objects.filter(created_at__date=today).count(),
        "orders_needs_review": Order.objects.filter(status=Order.Status.NEEDS_REVIEW).count(),
        "revenue_minor": revenue_minor,
        "low_stock_products": [
            product
            for product in Product.objects.filter(status=Product.Status.ACTIVE)
            if inventory_service.available_count(product) < 5
        ],
    }
    review_orders = Order.objects.filter(status=Order.Status.NEEDS_REVIEW).order_by("-updated_at")[
        :20
    ]
    return render(
        request,
        "marketplace/manage/dashboard.html",
        {"stats": stats, "review_orders": review_orders, "settings": MarketplaceSettings.load()},
    )


@login_required
@permission_required("marketplace.import_inventory", raise_exception=True)
def import_inventory(request):
    if request.method == "POST":
        form = ImportKeysForm(request.POST)
        if form.is_valid():
            batch = inventory_service.import_keys(
                form.cleaned_data["product"],
                form.cleaned_keys(),
                request.user,
                notes=form.cleaned_data.get("notes", ""),
            )
            audit_service.log(
                request.user,
                "inventory_import",
                target=batch,
                summary=f"Imported {batch.imported_count} keys for {batch.product}",
                ip=client_ip(request),
            )
            messages.success(
                request,
                f"Imported {batch.imported_count} keys "
                f"({batch.duplicate_count} duplicates, {batch.invalid_count} invalid).",
            )
            return redirect("marketplace:manage_import_inventory")
    else:
        form = ImportKeysForm()

    recent_batches = InventoryImportBatch.objects.select_related("product").order_by("-created_at")[
        :20
    ]
    return render(
        request,
        "marketplace/manage/import_inventory.html",
        {"form": form, "recent_batches": recent_batches},
    )


@login_required
@permission_required("marketplace.view_order", raise_exception=True)
def manage_order_detail(request, reference):
    order = get_object_or_404(Order.objects.select_related("user"), reference=reference)
    items = order.items.select_related("product").all()
    audit_entries = order.audit_log.all()[:50]
    payments = order.payments.all()
    refunds = order.refunds.select_related("payment").all()
    disputes = order.disputes.all()
    paying_payment = refunds_service.paying_payment(order)
    return render(
        request,
        "marketplace/manage/order_detail.html",
        {
            "order": order,
            "items": items,
            "audit_entries": audit_entries,
            "payments": payments,
            "refunds": refunds,
            "disputes": disputes,
            "refundable_minor": (
                refunds_service.refundable_minor(paying_payment) if paying_payment else 0
            ),
        },
    )


@login_required
@permission_required("marketplace.fulfill_order", raise_exception=True)
@require_POST
def redrive_fulfillment(request, reference):
    order = get_object_or_404(Order, reference=reference)
    from marketplace.services import fulfillment as fulfillment_service

    result = fulfillment_service.fulfill_order(order.pk)
    audit_service.log(
        request.user,
        "manual_fulfillment_redrive",
        target=order,
        summary=f"Re-drove fulfilment: {result}",
        ip=client_ip(request),
    )
    messages.info(request, f"Fulfilment re-drive result: {result}")
    return redirect("marketplace:manage_order_detail", reference=order.reference)


@login_required
@permission_required("marketplace.refund_order", raise_exception=True)
@require_POST
def initiate_refund(request, reference):
    order = get_object_or_404(Order, reference=reference)
    reason = request.POST.get("reason", "")
    payment = refunds_service.paying_payment(order)
    if payment is None:
        messages.error(request, "No paid payment found for this order.")
        return redirect("marketplace:manage_order_detail", reference=order.reference)

    raw_amount = request.POST.get("amount_minor") or refunds_service.refundable_minor(payment)
    try:
        amount_minor = int(raw_amount)
    except (TypeError, ValueError):
        messages.error(request, "Enter a valid refund amount.")
        return redirect("marketplace:manage_order_detail", reference=order.reference)

    try:
        refund = refunds_service.request_refund(order, payment, amount_minor, reason, request.user)
    except refunds_service.RefundError as exc:
        messages.error(request, str(exc))
        return redirect("marketplace:manage_order_detail", reference=order.reference)

    refund = refunds_service.submit_refund(refund)

    audit_service.log(
        request.user,
        "refund_initiated",
        target=order,
        summary=f"Refund #{refund.pk} for {amount_minor} {order.currency} ({refund.status})",
        ip=client_ip(request),
    )
    if refund.status == Refund.Status.COMPLETED:
        messages.success(request, "Refund completed.")
    elif refund.status == Refund.Status.FAILED:
        messages.error(request, f"Refund failed: {refund.failure_reason}")
    else:
        messages.info(request, "Refund submitted and is awaiting confirmation from the provider.")
    return redirect("marketplace:manage_order_detail", reference=order.reference)


@login_required
@permission_required("marketplace.reveal_key", raise_exception=True)
@require_POST
def admin_reveal_key(request, key_id):
    key = get_object_or_404(GameKey, pk=key_id)
    reason = request.POST.get("reason", "")
    plaintext_key = keys_service.decrypt(key.encrypted_key)
    keys_service.record_access(
        request.user,
        key,
        None,
        KeyAccessLog.Action.REVEAL,
        client_ip(request),
        request.META.get("HTTP_USER_AGENT", ""),
    )
    audit_service.log(
        request.user,
        "admin_key_reveal",
        target=key,
        summary=f"Admin revealed key #{key.pk}: {reason}",
        ip=client_ip(request),
    )
    return render(
        request,
        "marketplace/manage/_key_reveal.html",
        {"key": key, "plaintext_key": plaintext_key},
    )


@login_required
@permission_required("marketplace.toggle_killswitch", raise_exception=True)
@require_POST
def toggle_killswitch(request):
    settings_row = MarketplaceSettings.load()
    field = request.POST.get("field")
    if field in {
        "marketplace_enabled",
        "purchases_enabled",
        "fulfillment_enabled",
        "stripe_enabled",
        "paypal_enabled",
    }:
        setattr(settings_row, field, not getattr(settings_row, field))
        settings_row.save()
        flags_service.invalidate()
        audit_service.log(
            request.user,
            "killswitch_toggle",
            target=settings_row,
            summary=f"{field} -> {getattr(settings_row, field)}",
            ip=client_ip(request),
        )
        messages.success(request, f"{field} is now {getattr(settings_row, field)}.")
    return redirect("marketplace:manage_dashboard")
