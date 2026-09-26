from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import MarketplaceSettings, WebhookEvent

from . import factories


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    MARKETPLACE_ALLOW_MOCK=True,
)
class WebhookBodySizeTests(TestCase):
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

    @override_settings(MARKETPLACE_WEBHOOK_MAX_BODY_BYTES=100)
    def test_content_length_over_the_limit_is_rejected_before_reading_the_body(self):
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b"x" * 500,
            content_type="application/json",
            CONTENT_LENGTH="500",
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @override_settings(MARKETPLACE_WEBHOOK_MAX_BODY_BYTES=100)
    def test_actual_body_over_the_limit_is_rejected_even_with_a_lying_content_length(self):
        response = self.client.generic(
            "POST",
            reverse("marketplace:webhook", args=["mock"]),
            data=b"x" * 500,
            content_type="application/json",
            CONTENT_LENGTH="10",
        )
        self.assertIn(response.status_code, (400, 413))
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @override_settings(MARKETPLACE_WEBHOOK_MAX_BODY_BYTES=262144)
    def test_a_normal_sized_payload_is_not_rejected_for_size(self):
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b'{"bad": "signature"}',
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 413)

    @override_settings(MARKETPLACE_WEBHOOK_MAX_BODY_BYTES=100)
    def test_non_numeric_content_length_is_rejected_cleanly(self):
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b"{}",
            content_type="application/json",
            CONTENT_LENGTH="not-a-number",
        )
        self.assertEqual(response.status_code, 400)


@override_settings(
    MARKETPLACE_ENABLED=True,
    MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY,
    MARKETPLACE_ALLOW_MOCK=True,
)
class WebhookIpRateLimitTests(TestCase):
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

    def _post(self, ip="203.0.113.5"):
        return self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b'{"bad": "signature"}',
            content_type="application/json",
            REMOTE_ADDR=ip,
        )

    def test_flooding_a_single_source_ip_is_eventually_throttled(self):
        statuses = [self._post().status_code for _ in range(150)]
        self.assertIn(429, statuses)
        self.assertTrue(all(s in (400, 429) for s in statuses))

    def test_throttling_is_scoped_per_ip_not_global(self):
        for _ in range(125):
            self._post(ip="203.0.113.5")
        response_other_ip = self._post(ip="203.0.113.6")
        self.assertEqual(response_other_ip.status_code, 400)

    def test_unrelated_forwarded_for_header_cannot_bypass_the_throttle(self):
        # MARKETPLACE_TRUSTED_PROXY_COUNT defaults to 0, so the client-forged header
        # below must be ignored entirely and REMOTE_ADDR used for the rate limit key.
        for i in range(125):
            self.client.post(
                reverse("marketplace:webhook", args=["mock"]),
                data=b'{"bad": "signature"}',
                content_type="application/json",
                REMOTE_ADDR="203.0.113.7",
                HTTP_X_FORWARDED_FOR=f"1.2.3.{i}",
            )
        response = self.client.post(
            reverse("marketplace:webhook", args=["mock"]),
            data=b'{"bad": "signature"}',
            content_type="application/json",
            REMOTE_ADDR="203.0.113.7",
            HTTP_X_FORWARDED_FOR="9.9.9.9",
        )
        self.assertEqual(response.status_code, 429)
