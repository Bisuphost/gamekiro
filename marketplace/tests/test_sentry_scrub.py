from django.test import SimpleTestCase

from gamekiro.sentry_scrub import scrub_sentry_event


class SentryScrubTests(SimpleTestCase):
    def test_stripe_signature_header_is_scrubbed(self):
        event = {"request": {"headers": {"Stripe-Signature": "t=1,v1=secret"}}}
        scrubbed = scrub_sentry_event(event, {})
        self.assertEqual(scrubbed["request"]["headers"]["Stripe-Signature"], "[Filtered]")

    def test_paypal_headers_are_scrubbed_case_insensitively(self):
        event = {
            "request": {
                "headers": {
                    "PAYPAL-TRANSMISSION-SIG": "sig",
                    "paypal-transmission-id": "id",
                    "Paypal-Auth-Algo": "algo",
                    "PayPal-Cert-Url": "https://x",
                }
            }
        }
        scrubbed = scrub_sentry_event(event, {})
        for header in event["request"]["headers"]:
            self.assertEqual(scrubbed["request"]["headers"][header], "[Filtered]")

    def test_authorization_and_cookie_headers_are_scrubbed(self):
        event = {
            "request": {"headers": {"Authorization": "Bearer sk_live_x", "Cookie": "sessionid=abc"}}
        }
        scrubbed = scrub_sentry_event(event, {})
        self.assertEqual(scrubbed["request"]["headers"]["Authorization"], "[Filtered]")
        self.assertEqual(scrubbed["request"]["headers"]["Cookie"], "[Filtered]")

    def test_unrelated_headers_are_left_untouched(self):
        event = {"request": {"headers": {"Accept": "application/json", "Host": "gamekiro.com"}}}
        scrubbed = scrub_sentry_event(event, {})
        self.assertEqual(scrubbed["request"]["headers"]["Accept"], "application/json")
        self.assertEqual(scrubbed["request"]["headers"]["Host"], "gamekiro.com")

    def test_secret_settings_values_in_request_data_are_scrubbed(self):
        event = {
            "request": {
                "data": {
                    "stripe_secret_key": "sk_live_abcdef",
                    "django_secret_key": "topsecret",
                    "site_url": "https://gamekiro.com",
                }
            }
        }
        scrubbed = scrub_sentry_event(event, {})
        self.assertEqual(scrubbed["request"]["data"]["stripe_secret_key"], "[Filtered]")
        self.assertEqual(scrubbed["request"]["data"]["django_secret_key"], "[Filtered]")
        self.assertEqual(scrubbed["request"]["data"]["site_url"], "https://gamekiro.com")

    def test_event_with_no_request_is_returned_unchanged(self):
        event = {"message": "boom"}
        self.assertEqual(scrub_sentry_event(event, {}), event)

    def test_event_with_no_headers_key_does_not_crash(self):
        event = {"request": {"url": "https://gamekiro.com/store/webhooks/stripe/"}}
        scrubbed = scrub_sentry_event(event, {})
        self.assertEqual(scrubbed, event)

    def test_scrubbing_returns_the_same_event_object(self):
        event = {"request": {"headers": {"Stripe-Signature": "x"}}}
        self.assertIs(scrub_sentry_event(event, {}), event)
