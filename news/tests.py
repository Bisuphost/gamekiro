from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Article


class ArticleTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="editor", password="password123", is_staff=True
        )
        self.reader = User.objects.create_user(username="reader", password="password123")

    def test_list_only_shows_published_articles(self):
        Article.objects.create(title="Live", slug="live", body="Body", is_published=True)
        Article.objects.create(title="Draft", slug="draft", body="Body", is_published=False)

        response = self.client.get(reverse("news:list"))

        self.assertContains(response, "Live")
        self.assertNotContains(response, "Draft")

    def test_list_hides_future_scheduled_articles(self):
        Article.objects.create(
            title="Future",
            slug="future",
            body="Body",
            is_published=True,
            published_at=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("news:list"))

        self.assertNotContains(response, "Future")

    def test_detail_returns_published_article(self):
        article = Article.objects.create(
            title="Live", slug="live", body="Full body text", is_published=True
        )

        response = self.client.get(reverse("news:detail", args=[article.slug]))

        self.assertContains(response, "Full body text")

    def test_detail_404s_for_unpublished_to_anonymous(self):
        article = Article.objects.create(
            title="Draft", slug="draft", body="Body", is_published=False
        )

        response = self.client.get(reverse("news:detail", args=[article.slug]))

        self.assertEqual(response.status_code, 404)

    def test_detail_404s_for_unpublished_to_regular_user(self):
        article = Article.objects.create(
            title="Draft", slug="draft", body="Body", is_published=False
        )
        self.client.login(username="reader", password="password123")

        response = self.client.get(reverse("news:detail", args=[article.slug]))

        self.assertEqual(response.status_code, 404)

    def test_staff_can_preview_unpublished_article(self):
        article = Article.objects.create(
            title="Draft", slug="draft", body="Preview body", is_published=False
        )
        self.client.login(username="editor", password="password123")

        response = self.client.get(reverse("news:detail", args=[article.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview body")

    def test_first_two_articles_are_featured_and_next_three_secondary(self):
        for i in range(6):
            Article.objects.create(
                title=f"Article {i}",
                slug=f"article-{i}",
                body="Body",
                is_published=True,
                published_at=timezone.now() - timezone.timedelta(days=i),
            )

        response = self.client.get(reverse("news:list"))

        self.assertEqual(len(response.context["featured_articles"]), 2)
        self.assertEqual(len(response.context["secondary_articles"]), 3)
        self.assertEqual(
            [a.title for a in response.context["featured_articles"]],
            ["Article 0", "Article 1"],
        )
        self.assertEqual(
            [a.title for a in response.context["secondary_articles"]],
            ["Article 2", "Article 3", "Article 4"],
        )
        self.assertEqual([a.title for a in response.context["articles"]], ["Article 5"])

    def test_search_filters_articles_by_title(self):
        Article.objects.create(
            title="Cozy Farming Sim Announced", slug="cozy-farm", body="Body", is_published=True
        )
        Article.objects.create(
            title="Racing Game Patch Notes", slug="racing-patch", body="Body", is_published=True
        )

        response = self.client.get(reverse("news:list"), {"q": "farming"})

        self.assertContains(response, "Cozy Farming Sim Announced")
        self.assertNotContains(response, "Racing Game Patch Notes")

    def test_hero_sections_do_not_repeat_on_page_two(self):
        for i in range(20):
            Article.objects.create(
                title=f"Article {i}",
                slug=f"article-{i}",
                body="Body",
                is_published=True,
                published_at=timezone.now() - timezone.timedelta(days=i),
            )

        response = self.client.get(reverse("news:list"), {"page": 2})

        self.assertNotIn("featured_articles", response.context)
        self.assertNotIn("secondary_articles", response.context)

    def test_articles_ordered_by_published_at_descending(self):
        Article.objects.create(
            title="Older",
            slug="older",
            body="Body",
            is_published=True,
            published_at=timezone.now() - timezone.timedelta(days=2),
        )
        Article.objects.create(
            title="Newer",
            slug="newer",
            body="Body",
            is_published=True,
            published_at=timezone.now() - timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("news:list"))

        self.assertLess(
            response.content.index(b"Newer"),
            response.content.index(b"Older"),
        )
