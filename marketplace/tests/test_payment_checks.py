from django.test import SimpleTestCase, override_settings

from marketplace.checks import check_stripe_configuration

PROD = {"DEBUG": False, "SETTINGS_MODULE": "gamekiro.settings.prod"}
GOOD = {
    "PAYMENT_PROVIDERS_ENABLED": ["stripe"],
    "STRIPE_SECRET_KEY": "sk_live_x",
    "STRIPE_WEBHOOK_SECRETS": ["whsec_x"],
    "SITE_URL": "https://gamekiro.com",
    "MARKETPLACE_RESERVATION_TTL_MINUTES": 35,
    **PROD,
}


def ids(**overrides):
    with override_settings(**{**GOOD, **overrides}):
        return [error.id for error in check_stripe_configuration(None)]


class StripeConfigurationCheckTests(SimpleTestCase):
    def test_valid_production_configuration_passes(self):
        self.assertEqual(ids(), [])

    def test_disabled_stripe_is_not_checked(self):
        self.assertEqual(ids(PAYMENT_PROVIDERS_ENABLED=[], STRIPE_SECRET_KEY=None), [])

    def test_missing_key_is_an_error(self):
        self.assertEqual(ids(STRIPE_SECRET_KEY=None), ["marketplace.E003"])

    def test_missing_webhook_secret_is_an_error(self):
        self.assertEqual(ids(STRIPE_WEBHOOK_SECRETS=[]), ["marketplace.E003"])

    def test_publishable_key_is_an_error(self):
        self.assertIn("marketplace.E004", ids(STRIPE_SECRET_KEY="pk_live_x"))

    def test_test_key_in_production_is_an_error(self):
        self.assertIn("marketplace.E005", ids(STRIPE_SECRET_KEY="sk_test_x"))

    def test_restricted_test_key_in_production_is_an_error(self):
        self.assertIn("marketplace.E005", ids(STRIPE_SECRET_KEY="rk_test_x"))

    def test_test_key_in_development_is_fine(self):
        self.assertEqual(
            ids(STRIPE_SECRET_KEY="sk_test_x", DEBUG=True, SETTINGS_MODULE="gamekiro.settings.dev"),
            [],
        )

    def test_live_key_in_development_is_an_error(self):
        self.assertIn(
            "marketplace.E006",
            ids(DEBUG=True, SETTINGS_MODULE="gamekiro.settings.dev", SITE_URL="http://localhost"),
        )

    def test_non_https_site_url_in_production_is_an_error(self):
        self.assertIn("marketplace.E007", ids(SITE_URL="http://gamekiro.com"))

    def test_short_reservation_ttl_is_an_error(self):
        self.assertIn("marketplace.E008", ids(MARKETPLACE_RESERVATION_TTL_MINUTES=30))


PAYPAL_GOOD = {
    "PAYMENT_PROVIDERS_ENABLED": ["paypal"],
    "PAYPAL_ENV": "live",
    "PAYPAL_CLIENT_ID": "id",
    "PAYPAL_CLIENT_SECRET": "secret",
    "PAYPAL_WEBHOOK_ID": "WH-1",
    "SITE_URL": "https://gamekiro.com",
    **PROD,
}


def paypal_ids(**overrides):
    from marketplace.checks import check_paypal_configuration

    with override_settings(**{**PAYPAL_GOOD, **overrides}):
        return [error.id for error in check_paypal_configuration(None)]


class PayPalConfigurationCheckTests(SimpleTestCase):
    def test_valid_production_configuration_passes(self):
        self.assertEqual(paypal_ids(), [])

    def test_disabled_paypal_is_not_checked(self):
        self.assertEqual(paypal_ids(PAYMENT_PROVIDERS_ENABLED=[], PAYPAL_CLIENT_ID=None), [])

    def test_each_missing_credential_is_an_error(self):
        for name in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID"):
            with self.subTest(name=name):
                self.assertEqual(paypal_ids(**{name: None}), ["marketplace.E009"])

    def test_unknown_environment_is_an_error(self):
        self.assertIn("marketplace.E010", paypal_ids(PAYPAL_ENV="staging"))

    def test_sandbox_in_production_is_an_error(self):
        self.assertIn("marketplace.E011", paypal_ids(PAYPAL_ENV="sandbox"))

    def test_live_in_development_is_an_error(self):
        self.assertIn(
            "marketplace.E012",
            paypal_ids(DEBUG=True, SETTINGS_MODULE="gamekiro.settings.dev", SITE_URL="http://x"),
        )

    def test_sandbox_in_development_is_fine(self):
        self.assertEqual(
            paypal_ids(
                PAYPAL_ENV="sandbox",
                DEBUG=True,
                SETTINGS_MODULE="gamekiro.settings.dev",
                SITE_URL="http://localhost",
            ),
            [],
        )

    def test_non_https_site_url_in_production_is_an_error(self):
        self.assertIn("marketplace.E013", paypal_ids(SITE_URL="http://gamekiro.com"))


class FraudControlCheckTests(SimpleTestCase):
    @override_settings(
        DEBUG=False,
        SETTINGS_MODULE="gamekiro.settings.prod",
        MARKETPLACE_REQUIRE_VERIFIED_EMAIL=True,
        SITE_URL="http://gamekiro.com",
    )
    def test_non_https_site_url_with_verification_required_in_production_is_an_error(self):
        from marketplace.checks import check_fraud_control_configuration

        ids = [e.id for e in check_fraud_control_configuration(None)]
        self.assertIn("marketplace.E014", ids)

    @override_settings(
        DEBUG=False,
        SETTINGS_MODULE="gamekiro.settings.prod",
        MARKETPLACE_REQUIRE_VERIFIED_EMAIL=True,
        SITE_URL="https://gamekiro.com",
        MARKETPLACE_TRUSTED_PROXY_COUNT=0,
    )
    def test_valid_production_configuration_passes(self):
        from marketplace.checks import check_fraud_control_configuration

        self.assertEqual(check_fraud_control_configuration(None), [])

    @override_settings(
        DEBUG=False,
        SETTINGS_MODULE="gamekiro.settings.prod",
        SITE_URL="https://gamekiro.com",
        MARKETPLACE_TRUSTED_PROXY_COUNT=-1,
    )
    def test_negative_trusted_proxy_count_is_an_error(self):
        from marketplace.checks import check_fraud_control_configuration

        ids = [e.id for e in check_fraud_control_configuration(None)]
        self.assertIn("marketplace.E015", ids)
