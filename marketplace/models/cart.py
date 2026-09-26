from django.conf import settings
from django.db import models

from core.models import TimestampedModel

from .catalog import Product


class Cart(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="marketplace_cart"
    )

    def __str__(self):
        return f"Cart({self.user})"


class CartItem(TimestampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["cart", "product"], name="uniq_cartitem_cart_product"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product}"


class WishlistItem(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="marketplace_wishlist_items",
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlisted_by")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"], name="uniq_wishlistitem_user_product"
            ),
        ]

    def __str__(self):
        return f"{self.user} wishlists {self.product}"
