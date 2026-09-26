from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from marketplace.models import Product, WishlistItem

from .decorators import require_marketplace_enabled


@login_required
@require_marketplace_enabled
def wishlist_view(request):
    items = WishlistItem.objects.filter(user=request.user).select_related(
        "product__game", "product__activation_platform", "product__region"
    )
    return render(request, "marketplace/wishlist.html", {"items": items})


@login_required
@require_marketplace_enabled
@require_POST
def toggle_wishlist(request):
    product = get_object_or_404(Product, pk=request.POST.get("product_id"))
    item, created = WishlistItem.objects.get_or_create(user=request.user, product=product)
    if not created:
        item.delete()

    if request.htmx:
        return render(
            request,
            "marketplace/_wishlist_button.html",
            {"product": product, "in_wishlist": created},
        )

    for candidate in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(candidate)
    return redirect("marketplace:wishlist")
