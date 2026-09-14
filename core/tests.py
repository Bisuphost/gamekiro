from django.test import TestCase
from django.urls import reverse


class LegalPageTests(TestCase):
    def test_privacy_policy_page_renders(self):
        response = self.client.get(reverse("core:privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy Policy")
        self.assertContains(response, "contact@gamekiro.com")

    def test_terms_of_service_page_renders(self):
        response = self.client.get(reverse("core:terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Terms of Service")

    def test_cookie_policy_page_renders(self):
        response = self.client.get(reverse("core:cookies"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sessionid")
        self.assertContains(response, "csrftoken")

    def test_footer_links_to_legal_pages(self):
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, reverse("core:privacy"))
        self.assertContains(response, reverse("core:terms"))
        self.assertContains(response, reverse("core:cookies"))
