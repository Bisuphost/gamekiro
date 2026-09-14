from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from reactions.models import Reaction

from .models import Game, Platform, Review


class GameDirectoryTests(TestCase):
	def setUp(self):
		self.pc = Platform.objects.create(name="PC", slug="pc")
		self.switch = Platform.objects.create(name="Switch", slug="switch")
		self.action_game = Game.objects.create(
			title="Action Quest", slug="action-quest", description="A fast adventure."
		)
		self.action_game.platforms.add(self.pc, self.switch)
		self.action_game.tags.add("action", "adventure")
		self.strategy_game = Game.objects.create(title="Strategy World", slug="strategy-world")
		self.strategy_game.platforms.add(self.pc)
		self.strategy_game.tags.add("strategy")

	def test_game_list_filters_by_platform_and_tag_together(self):
		response = self.client.get(
			reverse("games:list"), {"platform": "switch", "tag": "action"}
		)

		self.assertContains(response, "Action Quest")
		self.assertNotContains(response, "Strategy World")

	def test_game_detail_displays_game(self):
		response = self.client.get(reverse("games:detail", args=[self.action_game.slug]))

		self.assertContains(response, "Action Quest")
		self.assertContains(response, "A fast adventure.")
		self.assertContains(response, "PC")

	def test_game_detail_returns_not_found_for_unknown_slug(self):
		response = self.client.get(reverse("games:detail", args=["missing-game"]))

		self.assertEqual(response.status_code, 404)


class GameSearchAutocompleteTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="player", password="password123")
		Game.objects.create(title="Action Quest", slug="action-quest")
		Game.objects.create(title="Strategy World", slug="strategy-world")

	def test_requires_login(self):
		response = self.client.get(reverse("games:search_autocomplete"), {"q": "quest"})
		self.assertEqual(response.status_code, 302)
		self.assertIn("/accounts/login", response.url)

	def test_matches_partial_case_insensitive_title(self):
		self.client.login(username="player", password="password123")
		response = self.client.get(reverse("games:search_autocomplete"), {"q": "quest"})
		self.assertContains(response, "Action Quest")
		self.assertNotContains(response, "Strategy World")

	def test_blank_query_returns_no_results(self):
		self.client.login(username="player", password="password123")
		response = self.client.get(reverse("games:search_autocomplete"), {"q": ""})
		self.assertNotContains(response, "Action Quest")
		self.assertNotContains(response, "Strategy World")

	def test_no_match_shows_empty_message(self):
		self.client.login(username="player", password="password123")
		response = self.client.get(reverse("games:search_autocomplete"), {"q": "nonexistent"})
		self.assertContains(response, "No games found")


class ReviewTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="player", password="password123")
		self.other_user = User.objects.create_user(username="rival", password="password123")
		self.game = Game.objects.create(title="Mirror Rift", slug="mirror-rift")

	def test_review_rating_must_be_between_1_and_5(self):
		review = Review(user=self.user, game=self.game, rating=0, body="Nope")
		with self.assertRaises(ValidationError):
			review.full_clean()

	def test_review_is_unique_per_user_game(self):
		Review.objects.create(user=self.user, game=self.game, rating=5, body="Great game")
		with self.assertRaises(IntegrityError):
			Review.objects.create(user=self.user, game=self.game, rating=4, body="Another review")

	def test_authenticated_user_can_create_and_update_review(self):
		self.client.login(username="player", password="password123")

		create_response = self.client.post(
			reverse("games:detail", args=[self.game.slug]),
			{"rating": 5, "body": "Loved it."},
		)
		self.assertEqual(create_response.status_code, 302)
		self.assertEqual(Review.objects.filter(user=self.user, game=self.game).count(), 1)

		review = Review.objects.get(user=self.user, game=self.game)
		self.assertEqual(review.rating, 5)
		self.assertEqual(review.body, "Loved it.")

		update_response = self.client.post(
			reverse("games:detail", args=[self.game.slug]),
			{"rating": 4, "body": "Still a blast."},
		)
		self.assertEqual(update_response.status_code, 302)
		self.assertEqual(Review.objects.filter(user=self.user, game=self.game).count(), 1)

		review.refresh_from_db()
		self.assertEqual(review.rating, 4)
		self.assertEqual(review.body, "Still a blast.")

	def test_review_create_requires_login(self):
		response = self.client.post(
			reverse("games:detail", args=[self.game.slug]),
			{"rating": 5, "body": "Too good to ignore."},
		)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/accounts/login", response.url)

	def test_game_detail_displays_average_rating_and_review_list(self):
		Review.objects.create(user=self.user, game=self.game, rating=5, body="Fantastic")
		Review.objects.create(user=self.other_user, game=self.game, rating=4, body="Strong")

		response = self.client.get(reverse("games:detail", args=[self.game.slug]))
		self.assertContains(response, "4.5")
		self.assertContains(response, "2 reviews")
		self.assertContains(response, "player")
		self.assertContains(response, "Fantastic")
		self.assertContains(response, "Strong")

	def test_helpful_reaction_works_for_reviews(self):
		review = Review.objects.create(user=self.user, game=self.game, rating=5, body="Amazing")
		self.client.login(username="rival", password="password123")

		response = self.client.post(reverse("reactions:toggle", args=["games", "review", review.pk]))
		self.assertEqual(response.status_code, 302)
		content_type = ContentType.objects.get_for_model(Review)
		self.assertEqual(
			Reaction.objects.filter(content_type=content_type, object_id=review.pk).count(),
			1,
		)
