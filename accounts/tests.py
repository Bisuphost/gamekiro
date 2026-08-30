from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Profile


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.other = User.objects.create_user(username="rival", password="password123")

    def test_profile_is_created_and_can_be_updated(self):
        self.assertTrue(Profile.objects.filter(user=self.user).exists())
        self.client.login(username="player", password="password123")
        response = self.client.post(
            reverse("accounts:profile-edit"),
            {"bio": "Ready to play", "discord_username": "player#1"},
        )
        self.assertRedirects(response, reverse("accounts:profile-detail", args=["player"]))
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile.bio, "Ready to play")

    def test_public_profile_displays_follow_counts(self):
        response = self.client.get(reverse("accounts:profile-detail", args=["rival"]))
        self.assertContains(response, "0 followers")
        self.assertContains(response, "0 following")

    def test_signup_creates_a_profile_with_all_profile_columns_available(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {
                "username": "new-player",
                "email": "new-player@example.com",
                "password1": "StrongPassword123!",
                "password2": "StrongPassword123!",
            },
        )

        self.assertRedirects(response, reverse("core:home"))
        self.assertTrue(Profile.objects.filter(user__username="new-player").exists())
