from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from games.models import Game

from .models import Profile, ProfileGame


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


class ProfileGameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.other = User.objects.create_user(username="rival", password="password123")
        self.game = Game.objects.create(title="Skyline", slug="skyline")

    def test_user_can_add_update_remove_profile_game_and_directory_link_is_present(self):
        self.client.login(username="player", password="password123")
        add_response = self.client.post(
            reverse("accounts:profile-game-add"),
            {"game": self.game.pk, "status": ProfileGame.COMPLETED},
        )
        self.assertRedirects(add_response, reverse("accounts:profile", args=["player"]))
        profile_game = ProfileGame.objects.get()
        self.assertContains(
            self.client.get(reverse("accounts:profile", args=["player"])),
            reverse("games:detail", args=[self.game.slug]),
        )

        self.client.post(
            reverse("accounts:profile-game-update", args=[profile_game.pk]),
            {"game": self.game.pk, "status": ProfileGame.DROPPED},
        )
        profile_game.refresh_from_db()
        self.assertEqual(profile_game.status, ProfileGame.DROPPED)

        self.client.post(reverse("accounts:profile-game-remove", args=[profile_game.pk]))
        self.assertFalse(ProfileGame.objects.exists())

    def test_duplicate_profile_game_and_unauthorized_modification_are_prevented(self):
        profile_game = ProfileGame.objects.create(profile=self.user.profile, game=self.game)
        self.client.login(username="player", password="password123")
        self.client.post(
            reverse("accounts:profile-game-add"),
            {"game": self.game.pk, "status": ProfileGame.WISHLIST},
        )
        self.assertEqual(ProfileGame.objects.count(), 1)

        self.client.login(username="rival", password="password123")
        response = self.client.post(
            reverse("accounts:profile-game-remove", args=[profile_game.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ProfileGame.objects.filter(pk=profile_game.pk).exists())
