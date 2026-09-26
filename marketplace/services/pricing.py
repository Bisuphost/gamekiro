from django.conf import settings

from . import coupons as coupons_service


def price_line_items(items, coupon=None):
    lines = []
    subtotal_minor = 0
    for product, quantity in items:
        line_total_minor = product.unit_price_minor * quantity
        subtotal_minor += line_total_minor
        lines.append(
            {
                "product": product,
                "quantity": quantity,
                "unit_price_minor": product.unit_price_minor,
                "line_total_minor": line_total_minor,
            }
        )

    discount_minor = 0
    if coupon is not None:
        discount_minor = coupons_service.compute_discount(coupon, subtotal_minor)

    tax_minor = 0
    total_minor = max(subtotal_minor - discount_minor, 0) + tax_minor

    return {
        "lines": lines,
        "subtotal_minor": subtotal_minor,
        "discount_minor": discount_minor,
        "tax_minor": tax_minor,
        "total_minor": total_minor,
        "currency": settings.MARKETPLACE_CURRENCY,
    }


def price_cart(cart, coupon=None):
    items = [(item.product, item.quantity) for item in cart.items.select_related("product")]
    return price_line_items(items, coupon=coupon)
