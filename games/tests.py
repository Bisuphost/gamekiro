from django.test import TestCase
from django.urls import reverse

from .models import Game, Platform


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
