from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
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

    def test_signup_creates_a_profile_and_redirects_to_onboarding(self):
        with patch("accounts.tasks.send_welcome_email.delay") as mock_delay:
            response = self.client.post(
                reverse("accounts:signup"),
                {
                    "username": "new-player",
                    "email": "new-player@example.com",
                    "password1": "StrongPassword123!",
                    "password2": "StrongPassword123!",
                },
            )

        self.assertRedirects(response, reverse("accounts:profile-setup"))
        created_user = User.objects.get(username="new-player")
        self.assertTrue(Profile.objects.filter(user=created_user).exists())
        mock_delay.assert_called_once_with(created_user.pk)

    def test_new_user_is_prompted_for_onboarding_and_can_skip_setup(self):
        user = User.objects.create_user(username="onboarded", password="password123")
        self.client.login(username="onboarded", password="password123")
        session = self.client.session
        session["onboarding_required"] = True
        session.save()

        response = self.client.get(reverse("core:home"))
        self.assertRedirects(response, reverse("accounts:profile-setup"))

        response = self.client.post(
            reverse("accounts:profile-setup"),
            {"skip": "1"},
        )
        self.assertRedirects(response, reverse("core:home"))
        self.assertNotIn("onboarding_required", self.client.session)

    def test_completed_users_are_not_repeatedly_prompted(self):
        user = User.objects.create_user(username="existing", password="password123")
        user.profile.bio = "Already set"
        user.profile.save(update_fields=["bio"])
        self.client.login(username="existing", password="password123")

        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "profile setup")

    def test_empty_states_render_for_no_data(self):
        self.client.login(username="player", password="password123")
        profile_response = self.client.get(reverse("accounts:profile", args=["player"]))
        self.assertContains(profile_response, "No games added yet")

        inbox_response = self.client.get(reverse("messaging:inbox"))
        self.assertContains(inbox_response, "No conversations yet")

        forum_response = self.client.get(reverse("forum:category_list"))
        self.assertContains(forum_response, "No categories yet")

    def test_404_and_500_templates_exist_and_404_renders(self):
        not_found_response = self.client.get("/no-such-page-please/")
        self.assertEqual(not_found_response.status_code, 404)
        self.assertContains(not_found_response, "Page not found", status_code=404)
        self.assertTrue(Path("templates/500.html").exists())

    def test_seed_demo_command_runs_idempotently(self):
        call_command("seed_demo")
        first_count = User.objects.filter(username__startswith="demo_").count()

        call_command("seed_demo")
        second_count = User.objects.filter(username__startswith="demo_").count()

        self.assertEqual(first_count, second_count)
        self.assertGreater(first_count, 0)


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
