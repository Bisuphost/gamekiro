from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import CartItem, MarketplaceSettings, WishlistItem

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class CartTests(TestCase):
    def setUp(self):
        cache.clear()
        MarketplaceSettings.objects.update_or_create(
            pk=1,
            defaults={
                "marketplace_enabled": True,
                "purchases_enabled": True,
                "fulfillment_enabled": True,
            },
        )
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product(unit_price_minor=1500, max_per_order=3)
        self.client.login(username="buyer", password="testpass123")

    def test_add_to_cart_creates_item(self):
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        self.assertTrue(CartItem.objects.filter(product=self.product).exists())

    def test_quantity_is_clamped_to_max_per_order(self):
        self.client.post(
            reverse("marketplace:cart_add"), {"product_id": self.product.pk, "quantity": 10}
        )
        item = CartItem.objects.get(product=self.product)
        self.assertEqual(item.quantity, 3)

    def test_remove_from_cart_deletes_item(self):
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        self.client.post(reverse("marketplace:cart_remove"), {"product_id": self.product.pk})
        self.assertFalse(CartItem.objects.filter(product=self.product).exists())

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("marketplace:cart"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_cart_count_reflects_total_quantity_across_items(self):
        other_product = factories.make_product(game=factories.make_game("Other Game"))
        self.client.post(
            reverse("marketplace:cart_add"), {"product_id": self.product.pk, "quantity": 2}
        )
        self.client.post(reverse("marketplace:cart_add"), {"product_id": other_product.pk})

        response = self.client.get(reverse("marketplace:cart_count"))
        self.assertContains(response, "3")

    def test_cart_count_badge_hidden_when_empty(self):
        response = self.client.get(reverse("marketplace:cart_count"))
        self.assertNotContains(response, "bg-red-600")

    def test_header_shows_accessible_cart_link_when_logged_in(self):
        response = self.client.get(reverse("marketplace:home"))
        self.assertContains(response, 'aria-label="Cart"')
        self.assertContains(response, reverse("marketplace:cart"))

    def test_add_to_cart_htmx_response_includes_out_of_band_count_update(self):
        response = self.client.post(
            reverse("marketplace:cart_add"),
            {"product_id": self.product.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(response, 'id="cart-count"')
        self.assertContains(response, "hx-swap-oob")

    def test_cart_page_full_load_has_no_stray_oob_badge(self):
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        response = self.client.get(reverse("marketplace:cart"))
        self.assertNotContains(response, "hx-swap-oob")


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class WishlistTests(TestCase):
    def setUp(self):
        cache.clear()
        MarketplaceSettings.objects.update_or_create(
            pk=1,
            defaults={
                "marketplace_enabled": True,
                "purchases_enabled": True,
                "fulfillment_enabled": True,
            },
        )
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product()
        self.client.login(username="buyer", password="testpass123")

    def test_toggle_adds_then_removes(self):
        self.client.post(reverse("marketplace:wishlist_toggle"), {"product_id": self.product.pk})
        self.assertTrue(WishlistItem.objects.filter(product=self.product).exists())

        self.client.post(reverse("marketplace:wishlist_toggle"), {"product_id": self.product.pk})
        self.assertFalse(WishlistItem.objects.filter(product=self.product).exists())
