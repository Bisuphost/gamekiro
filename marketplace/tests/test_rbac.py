from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import GameKey, MarketplaceSettings, Order, Refund
from marketplace.providers import mock as mock_provider
from marketplace.services import cart as cart_service
from marketplace.services import checkout as checkout_service
from marketplace.services import refunds as refunds_service

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class RbacPermissionMatrixTests(TestCase):
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
        self.staff = factories.make_user("staff")
        self.support = factories.make_user("support")
        self.support.groups.add(Group.objects.get(name="Marketplace Support"))
        self.manager = factories.make_user("manager")
        self.manager.groups.add(Group.objects.get(name="Marketplace Manager"))
        self.finance = factories.make_user("finance")
        self.finance.groups.add(Group.objects.get(name="Marketplace Finance"))
        self.admin = factories.make_user("mp_admin")
        self.admin.groups.add(Group.objects.get(name="Marketplace Admin"))
        self.customer = factories.make_user("customer")

        self.product = factories.make_product(unit_price_minor=500)
        factories.make_keys(self.product, 1, self.staff)

        self.client.login(username="customer", password="testpass123")
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        self.client.post(reverse("marketplace:checkout_confirm"))
        self.order = Order.objects.get(user=self.customer)
        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", self.order, self.order.total_minor, self.order.currency
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=raw_body,
                content_type="application/json",
                HTTP_X_MOCK_SIGNATURE=signature,
            )
        self.client.logout()

    def _login(self, username):
        self.client.logout()
        self.client.login(username=username, password="testpass123")

    def test_support_can_view_orders_and_redrive_but_not_refund_or_import(self):
        self._login("support")
        self.assertEqual(
            self.client.get(
                reverse("marketplace:manage_order_detail", args=[self.order.reference])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_order_redrive", args=[self.order.reference])
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_order_refund", args=[self.order.reference]),
                {"amount_minor": 500},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("marketplace:manage_import_inventory")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_killswitch"), {"field": "purchases_enabled"}
            ).status_code,
            403,
        )

    def test_manager_can_import_but_not_refund_or_toggle_killswitch(self):
        self._login("manager")
        self.assertEqual(
            self.client.get(reverse("marketplace:manage_import_inventory")).status_code, 200
        )
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_order_refund", args=[self.order.reference]),
                {"amount_minor": 500},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_killswitch"), {"field": "purchases_enabled"}
            ).status_code,
            403,
        )

    def test_finance_can_refund_but_not_import_or_toggle_killswitch(self):
        self._login("finance")
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_order_refund", args=[self.order.reference]),
                {"amount_minor": 500},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.get(reverse("marketplace:manage_import_inventory")).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_killswitch"), {"field": "purchases_enabled"}
            ).status_code,
            403,
        )

    def test_admin_group_can_toggle_killswitch_and_reveal_key(self):
        self._login("mp_admin")
        key = GameKey.objects.filter(product=self.product).first()
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_killswitch"), {"field": "purchases_enabled"}
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                reverse("marketplace:manage_key_reveal", args=[key.pk]), {"reason": "audit"}
            ).status_code,
            200,
        )

    def test_customer_with_no_group_is_blocked_from_every_manage_route(self):
        self._login("customer")
        self.assertEqual(self.client.get(reverse("marketplace:manage_dashboard")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("marketplace:manage_import_inventory")).status_code, 403
        )
        self.assertEqual(
            self.client.get(
                reverse("marketplace:manage_order_detail", args=[self.order.reference])
            ).status_code,
            403,
        )


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class RefundAdminLockdownTests(TestCase):
    """Refund mutations must only happen through refunds_service (provider call,
    order transition, key revocation, audit log) — never through a raw model
    save in Django admin, even for a user holding the seeded add/change_refund
    model permissions."""

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
        self.finance = factories.make_user("finance")
        self.finance.is_staff = True
        self.finance.save()
        self.finance.groups.add(Group.objects.get(name="Marketplace Finance"))
        self.client.login(username="finance", password="testpass123")

    def test_finance_group_cannot_add_a_refund_via_django_admin(self):
        self.assertTrue(self.finance.has_perm("marketplace.add_refund"))
        response = self.client.get("/admin/marketplace/refund/add/")
        self.assertEqual(response.status_code, 403)

    def test_finance_group_cannot_change_a_refund_via_django_admin(self):
        order, payment, intent = self._paid_order()
        refund = refunds_service.request_refund(order, payment, 100, "test", self.finance)
        self.assertTrue(self.finance.has_perm("marketplace.change_refund"))
        url = f"/admin/marketplace/refund/{refund.pk}/change/"
        # has_view_permission is still True, so the page itself renders read-only
        # (200) rather than 403 — the real guarantee is that has_change_permission
        # blocks the actual save, which Django admin enforces on POST.
        response = self.client.post(url, {"status": "completed", "amount_minor": "999999"})
        self.assertEqual(response.status_code, 403)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.REQUESTED)
        self.assertEqual(refund.amount_minor, 100)

    def _paid_order(self):
        staff = factories.make_user("rbac-staff")
        buyer = factories.make_user("rbac-buyer")
        product = factories.make_product(unit_price_minor=1000)
        factories.make_keys(product, 1, staff)
        cart_service.add_item(buyer, product, 1)
        order, payment, intent = checkout_service.create_order(buyer, provider_slug="mock")
        raw_body, signature = mock_provider.build_signed_event(
            "payment_succeeded", order, order.total_minor, order.currency
        )
        from marketplace.services import webhooks as webhooks_service

        with self.captureOnCommitCallbacks(execute=True):
            webhooks_service.handle("mock", raw_body, {"X-Mock-Signature": signature})
        payment.refresh_from_db()
        return order, payment, intent
