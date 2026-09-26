from django.core.checks import Error
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace import providers
from marketplace.checks import check_mock_provider_not_in_production

from . import factories


class MockProviderLockdownTests(TestCase):
    @override_settings(MARKETPLACE_ALLOW_MOCK=False)
    def test_registry_refuses_mock_when_not_allowed(self):
        with self.assertRaises(ValueError):
            providers.get_provider("mock")

    @override_settings(MARKETPLACE_ALLOW_MOCK=True)
    def test_registry_serves_mock_when_allowed(self):
        self.assertEqual(providers.get_provider("mock").slug, "mock")

    @override_settings(MARKETPLACE_ALLOW_MOCK=False)
    def test_mock_webhook_route_is_404_when_not_allowed(self):
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    @override_settings(MARKETPLACE_ALLOW_MOCK=False)
    def test_mock_checkout_page_is_404_when_not_allowed(self):
        buyer = factories.make_user("buyer")
        self.client.login(username="buyer", password="testpass123")
        response = self.client.get(reverse("marketplace:mock_checkout", args=["ORD-XXXXXXXX"]))
        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(buyer)


class MockProviderSystemCheckTests(TestCase):
    @override_settings(
        MARKETPLACE_ALLOW_MOCK=True, DEBUG=False, SETTINGS_MODULE="gamekiro.settings.prod"
    )
    def test_check_errors_when_mock_enabled_in_production(self):
        errors = check_mock_provider_not_in_production(None)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Error)
        self.assertEqual(errors[0].id, "marketplace.E002")

    @override_settings(MARKETPLACE_ALLOW_MOCK=False, DEBUG=False)
    def test_check_passes_when_mock_disabled(self):
        self.assertEqual(check_mock_provider_not_in_production(None), [])

    @override_settings(MARKETPLACE_ALLOW_MOCK=True, DEBUG=True)
    def test_check_passes_in_debug(self):
        self.assertEqual(check_mock_provider_not_in_production(None), [])
