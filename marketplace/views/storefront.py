from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from core.seo import absolute_media_url, absolute_url
from marketplace.models import ActivationPlatform, Product, Region, WishlistItem
from marketplace.services import inventory as inventory_service

from .decorators import require_marketplace_enabled

SORT_OPTIONS = {
    "newest": "-created_at",
    "price_low": "unit_price_minor",
    "price_high": "-unit_price_minor",
    "title": "game__title",
}


@require_marketplace_enabled
def home(request):
    base_qs = Product.objects.filter(
        status=Product.Status.ACTIVE, is_purchasable=True
    ).select_related("game", "activation_platform", "region")
    featured_products = base_qs.order_by("-created_at")[:8]
    deals = base_qs.filter(compare_at_price_minor__isnull=False).order_by("-created_at")[:8]
    return render(
        request,
        "marketplace/home.html",
        {"featured_products": featured_products, "deals": deals},
    )


@require_marketplace_enabled
def browse(request):
    products = Product.objects.filter(
        status=Product.Status.ACTIVE, is_purchasable=True
    ).select_related("game", "activation_platform", "region")

    query = request.GET.get("q", "").strip()
    platform_slug = request.GET.get("platform", "")
    region_code = request.GET.get("region", "")
    sort = request.GET.get("sort", "newest")

    if query:
        products = products.filter(game__title__icontains=query)
    if platform_slug:
        products = products.filter(activation_platform__slug=platform_slug)
    if region_code:
        products = products.filter(region__code=region_code)

    products = products.order_by(SORT_OPTIONS.get(sort, "-created_at"))

    return render(
        request,
        "marketplace/browse.html",
        {
            "products": products,
            "platforms": ActivationPlatform.objects.all(),
            "regions": Region.objects.all(),
            "query": query,
            "selected_platform": platform_slug,
            "selected_region": region_code,
            "selected_sort": sort,
        },
    )


@require_marketplace_enabled
def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related("game", "activation_platform", "region").prefetch_related(
            "images"
        ),
        slug=slug,
        status=Product.Status.ACTIVE,
    )
    available = inventory_service.available_count(product)
    in_wishlist = False
    if request.user.is_authenticated:
        in_wishlist = WishlistItem.objects.filter(user=request.user, product=product).exists()

    return render(
        request,
        "marketplace/product_detail.html",
        {
            "product": product,
            "available_count": available,
            "in_wishlist": in_wishlist,
            "structured_data": _product_structured_data(product, available),
        },
    )


def _product_structured_data(product, available_count):
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": f"{product.game.title} ({product.activation_platform.name}, {product.region.name})",
        "url": absolute_url(reverse("marketplace:product_detail", args=[product.slug])),
        "offers": {
            "@type": "Offer",
            "price": f"{product.unit_price_minor / 100:.2f}",
            "priceCurrency": product.currency,
            "availability": (
                "https://schema.org/InStock"
                if available_count > 0
                else "https://schema.org/OutOfStock"
            ),
            "url": absolute_url(reverse("marketplace:product_detail", args=[product.slug])),
        },
    }
    if product.game.cover_image:
        data["image"] = absolute_media_url(product.game.cover_image.url)
    if product.description:
        data["description"] = product.description
    return data
