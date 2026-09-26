from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import GameKey, MarketplaceSettings

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class AdminSurfaceTests(TestCase):
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
        self.manager = factories.make_user("manager")
        self.manager.groups.add(Group.objects.get(name="Marketplace Manager"))
        self.customer = factories.make_user("customer")
        self.product = factories.make_product()

    def test_customer_cannot_reach_dashboard(self):
        self.client.login(username="customer", password="testpass123")
        response = self.client.get(reverse("marketplace:manage_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_manager_can_reach_dashboard(self):
        self.client.login(username="manager", password="testpass123")
        response = self.client.get(reverse("marketplace:manage_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_manager_can_import_keys(self):
        self.client.login(username="manager", password="testpass123")
        response = self.client.post(
            reverse("marketplace:manage_import_inventory"),
            {
                "product": self.product.pk,
                "keys_text": "ABCD-1234\nEFGH-5678\n",
                "notes": "test batch",
            },
        )
        self.assertRedirects(response, reverse("marketplace:manage_import_inventory"))
        self.assertEqual(
            GameKey.objects.filter(product=self.product, status=GameKey.Status.AVAILABLE).count(),
            2,
        )

    def test_customer_cannot_import_keys(self):
        self.client.login(username="customer", password="testpass123")
        response = self.client.post(
            reverse("marketplace:manage_import_inventory"),
            {"product": self.product.pk, "keys_text": "ABCD-1234\n"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(GameKey.objects.filter(product=self.product).exists())

    def test_marketplace_disabled_returns_503(self):
        MarketplaceSettings.objects.filter(pk=1).update(marketplace_enabled=False)
        cache.clear()
        response = self.client.get(reverse("marketplace:home"))
        self.assertEqual(response.status_code, 503)
