from django.db.models import Sum

from marketplace.models import Cart, CartItem, Product


class CartError(Exception):
    pass


def get_or_create_cart(user):
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def item_count(user):
    if not user.is_authenticated:
        return 0
    return CartItem.objects.filter(cart__user=user).aggregate(total=Sum("quantity"))["total"] or 0


def add_item(user, product, quantity=1):
    if product.status != Product.Status.ACTIVE or not product.is_purchasable:
        raise CartError("This product is not available for purchase.")
    if quantity < 1:
        raise CartError("Quantity must be at least 1.")

    cart = get_or_create_cart(user)
    item, created = CartItem.objects.get_or_create(
        cart=cart, product=product, defaults={"quantity": min(quantity, product.max_per_order)}
    )
    if not created:
        item.quantity = min(item.quantity + quantity, product.max_per_order)
        item.save()
    return item


def update_quantity(user, product_id, quantity):
    cart = get_or_create_cart(user)
    if quantity <= 0:
        CartItem.objects.filter(cart=cart, product_id=product_id).delete()
        return None
    try:
        item = CartItem.objects.select_related("product").get(cart=cart, product_id=product_id)
    except CartItem.DoesNotExist as exc:
        raise CartError("That item is not in your cart.") from exc
    item.quantity = min(quantity, item.product.max_per_order)
    item.save()
    return item


def remove_item(user, product_id):
    cart = get_or_create_cart(user)
    CartItem.objects.filter(cart=cart, product_id=product_id).delete()


def validate_cart(cart):
    problems = []
    for item in list(cart.items.select_related("product")):
        product = item.product
        if product.status != Product.Status.ACTIVE or not product.is_purchasable:
            problems.append(f"{product} is no longer available and was removed from your cart.")
            item.delete()
            continue
        if item.quantity > product.max_per_order:
            item.quantity = product.max_per_order
            item.save()
            problems.append(
                f"Quantity for {product} was reduced to the maximum allowed "
                f"({product.max_per_order})."
            )
    return problems
