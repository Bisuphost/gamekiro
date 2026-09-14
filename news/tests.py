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
