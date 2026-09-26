from unittest import mock

from django.core import signing
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from marketplace.models import BuyerVerification, MarketplaceSettings, OutboundEmail
from marketplace.services import verification as verification_service

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class VerificationServiceTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("buyer", verified=False)

    def test_unverified_user_is_not_verified(self):
        self.assertFalse(verification_service.is_verified(self.user))

    def test_verifying_with_the_mailed_token_marks_the_user_verified(self):
        token = verification_service.make_token(self.user)
        verification_service.verify_token(token, expected_user_id=self.user.pk)
        self.assertTrue(verification_service.is_verified(self.user))

    def test_user_with_no_email_can_never_be_verified(self):
        self.user.email = ""
        self.user.save()
        BuyerVerification.objects.create(
            user=self.user, email="", verified_at=verification_service.timezone.now()
        )
        self.assertFalse(verification_service.is_verified(self.user))

    def test_tampered_token_payload_is_rejected(self):
        token = verification_service.make_token(self.user)
        payload, timestamp, sig = token.split(":")
        forged_payload = signing.b64_encode(b'{"user_id":1,"email":"evil@x.com"}').decode()
        tampered = f"{forged_payload}:{timestamp}:{sig}"
        with self.assertRaises(verification_service.InvalidVerificationToken):
            verification_service.verify_token(tampered, expected_user_id=self.user.pk)
        self.assertFalse(verification_service.is_verified(self.user))

    def test_tampered_signature_is_rejected(self):
        token = verification_service.make_token(self.user)
        payload, timestamp, sig = token.split(":")
        flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
        tampered = f"{payload}:{timestamp}:{flipped}"
        with self.assertRaises(verification_service.InvalidVerificationToken):
            verification_service.verify_token(tampered, expected_user_id=self.user.pk)

    def test_token_for_a_different_user_is_rejected_even_if_correctly_signed(self):
        other = factories.make_user("other", verified=False)
        token = verification_service.make_token(other)
        with self.assertRaises(verification_service.InvalidVerificationToken):
            verification_service.verify_token(token, expected_user_id=self.user.pk)
        self.assertFalse(verification_service.is_verified(other))

    def test_garbage_token_is_rejected(self):
        with self.assertRaises(verification_service.InvalidVerificationToken):
            verification_service.verify_token("not-a-real-token", expected_user_id=self.user.pk)

    def test_empty_token_is_rejected(self):
        with self.assertRaises(verification_service.InvalidVerificationToken):
            verification_service.verify_token("", expected_user_id=self.user.pk)

    def test_expired_token_is_rejected(self):
        token = verification_service.make_token(self.user)
        with mock.patch.object(verification_service, "TOKEN_MAX_AGE_SECONDS", -1):
            with self.assertRaises(verification_service.InvalidVerificationToken):
                verification_service.verify_token(token, expected_user_id=self.user.pk)
        self.assertFalse(verification_service.is_verified(self.user))

    def test_a_token_signed_with_a_different_salt_is_rejected(self):
        forged = signing.dumps(
            {"user_id": self.user.pk, "email": self.user.email}, salt="some-other-salt"
        )
        with self.assertRaises(verification_service.InvalidVerificationToken):
            verification_service.verify_token(forged, expected_user_id=self.user.pk)

    def test_changing_email_requires_re_verification(self):
        token = verification_service.make_token(self.user)
        verification_service.verify_token(token, expected_user_id=self.user.pk)
        self.assertTrue(verification_service.is_verified(self.user))

        self.user.email = "new-address@example.test"
        self.user.save()
        self.assertFalse(verification_service.is_verified(self.user))

    def test_reverifying_an_old_email_after_switching_back_does_not_replay_stale_state(self):
        token = verification_service.make_token(self.user)
        verification_service.verify_token(token, expected_user_id=self.user.pk)
        self.user.email = "new-address@example.test"
        self.user.save()
        self.user.refresh_from_db()
        self.assertFalse(verification_service.is_verified(self.user))
        new_token = verification_service.make_token(self.user)
        verification_service.verify_token(new_token, expected_user_id=self.user.pk)
        self.user.refresh_from_db()
        self.assertTrue(verification_service.is_verified(self.user))

    def test_send_verification_email_queues_exactly_one_outbound_email(self):
        verification_service.send_verification_email(self.user)
        self.assertEqual(OutboundEmail.objects.filter(to_email=self.user.email).count(), 1)
        email = OutboundEmail.objects.get(to_email=self.user.email)
        self.assertIn("http", email.body_text)
        self.assertNotIn(self.user.password, email.body_text)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class VerificationGateViewTests(TestCase):
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
        self.buyer = factories.make_user("buyer", verified=False)
        self.product = factories.make_product(unit_price_minor=1999)
        factories.make_keys(self.product, 3, self.staff)
        self.client.login(username="buyer", password="testpass123")

    def test_unverified_user_is_redirected_away_from_checkout_page(self):
        response = self.client.get(reverse("marketplace:checkout"))
        self.assertRedirects(response, reverse("marketplace:verify_email"))

    def test_unverified_user_cannot_confirm_checkout_even_by_posting_directly(self):
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        response = self.client.post(reverse("marketplace:checkout_confirm"), {"provider": "mock"})
        self.assertRedirects(response, reverse("marketplace:verify_email"))
        self.assertEqual(len(self.client.session.get("marketplace_coupon_code", "")), 0)

    @override_settings(MARKETPLACE_ALLOW_MOCK=True, PAYMENT_PROVIDERS_ENABLED=["mock"])
    def test_verified_user_can_reach_checkout(self):
        self.client.post(reverse("marketplace:cart_add"), {"product_id": self.product.pk})
        BuyerVerification.objects.update_or_create(
            user=self.buyer,
            defaults={"email": self.buyer.email, "verified_at": self.buyer.date_joined},
        )
        response = self.client.post(reverse("marketplace:checkout_confirm"), {"provider": "mock"})
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, reverse("marketplace:verify_email"))

    def test_first_visit_to_verify_page_auto_sends_the_email(self):
        self.client.get(reverse("marketplace:verify_email"))
        self.assertEqual(OutboundEmail.objects.filter(to_email=self.buyer.email).count(), 1)

    def test_second_visit_does_not_send_a_second_email(self):
        self.client.get(reverse("marketplace:verify_email"))
        self.client.get(reverse("marketplace:verify_email"))
        self.assertEqual(OutboundEmail.objects.filter(to_email=self.buyer.email).count(), 1)

    def test_resend_button_sends_another_email(self):
        self.client.get(reverse("marketplace:verify_email"))
        self.client.post(reverse("marketplace:verify_email_resend"))
        self.assertEqual(OutboundEmail.objects.filter(to_email=self.buyer.email).count(), 2)

    def test_resend_is_rate_limited(self):
        for _ in range(3):
            self.client.post(reverse("marketplace:verify_email_resend"))
        before = OutboundEmail.objects.filter(to_email=self.buyer.email).count()
        self.client.post(reverse("marketplace:verify_email_resend"))
        after = OutboundEmail.objects.filter(to_email=self.buyer.email).count()
        self.assertEqual(before, after)

    def test_resend_requires_post(self):
        response = self.client.get(reverse("marketplace:verify_email_resend"))
        self.assertEqual(response.status_code, 405)

    def test_confirm_link_verifies_and_redirects_to_checkout(self):
        token = verification_service.make_token(self.buyer)
        response = self.client.get(reverse("marketplace:verify_email_confirm", args=[token]))
        self.assertRedirects(response, reverse("marketplace:checkout"))
        self.buyer.refresh_from_db()
        self.assertTrue(verification_service.is_verified(self.buyer))

    def test_confirm_link_requires_login_and_does_not_leak_verification_without_it(self):
        self.client.logout()
        token = verification_service.make_token(self.buyer)
        response = self.client.get(reverse("marketplace:verify_email_confirm", args=[token]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
        self.buyer.refresh_from_db()
        self.assertFalse(verification_service.is_verified(self.buyer))

    def test_confirm_link_cannot_be_used_by_a_different_logged_in_account(self):
        token = verification_service.make_token(self.buyer)
        intruder = factories.make_user("intruder", verified=False)
        self.client.logout()
        self.client.login(username="intruder", password="testpass123")
        self.client.get(reverse("marketplace:verify_email_confirm", args=[token]))
        self.buyer.refresh_from_db()
        intruder.refresh_from_db()
        self.assertFalse(verification_service.is_verified(self.buyer))
        self.assertFalse(verification_service.is_verified(intruder))

    def test_malformed_token_in_the_url_does_not_crash_the_view(self):
        base = reverse("marketplace:verify_email_confirm", args=["placeholder"])
        malicious_path = base.replace("placeholder", "not%00a$$token!!")
        response = self.client.get(malicious_path)
        self.assertRedirects(response, reverse("marketplace:verify_email"))
        self.buyer.refresh_from_db()
        self.assertFalse(verification_service.is_verified(self.buyer))

    @override_settings(MARKETPLACE_REQUIRE_VERIFIED_EMAIL=False)
    def test_flag_off_disables_the_gate_entirely(self):
        response = self.client.get(reverse("marketplace:checkout"))
        self.assertEqual(response.status_code, 200)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class MailBombingProtectionTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_many_accounts_sharing_one_email_are_capped_together(self):
        victim_email = "victim@example.test"
        sent = 0
        for i in range(10):
            user = factories.make_user(f"fake{i}", verified=False, email=victim_email)
            try:
                verification_service.send_verification_email(user)
                sent += 1
            except verification_service.SendSuppressed:
                pass
        self.assertEqual(sent, verification_service.EMAIL_SEND_LIMIT)
        self.assertEqual(OutboundEmail.objects.filter(to_email=victim_email).count(), sent)

    def test_a_different_email_address_is_not_affected_by_another_ones_cap(self):
        for i in range(10):
            user = factories.make_user(f"fake{i}", verified=False, email="victim@example.test")
            try:
                verification_service.send_verification_email(user)
            except verification_service.SendSuppressed:
                pass
        other = factories.make_user("legit", verified=False, email="legit@example.test")
        verification_service.send_verification_email(other)
        self.assertEqual(OutboundEmail.objects.filter(to_email="legit@example.test").count(), 1)

    def test_email_address_matching_is_case_insensitive(self):
        for i in range(verification_service.EMAIL_SEND_LIMIT):
            user = factories.make_user(f"fake{i}", verified=False, email="Victim@Example.test")
            verification_service.send_verification_email(user)
        blocked = factories.make_user("blocked", verified=False, email="victim@example.test")
        with self.assertRaises(verification_service.SendSuppressed):
            verification_service.send_verification_email(blocked)

    def test_user_with_no_email_is_a_silent_no_op(self):
        user = factories.make_user("noemail", verified=False, email="")
        verification_service.send_verification_email(user)
        self.assertEqual(OutboundEmail.objects.count(), 0)
