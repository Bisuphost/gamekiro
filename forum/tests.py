from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Category, Post, Thread
from .views import POSTS_PER_PAGE, THREADS_PER_PAGE


class CategoryDetailSearchFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")

    def _detail_url(self, **params):
        url = reverse("forum:category_detail", args=[self.category.slug])
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_search_matches_title_icontains(self):
        Thread.objects.create(
            category=self.category, author=self.user, title="Best RPGs 2026", slug="rpgs"
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Platformer tier list", slug="plat"
        )
        response = self.client.get(self._detail_url(q="rpg"))
        self.assertContains(response, "Best RPGs 2026")
        self.assertNotContains(response, "Platformer tier list")

    def test_search_does_not_match_post_body(self):
        thread = Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Off-topic chat",
            slug="off-topic-chat",
        )
        Post.objects.create(thread=thread, author=self.user, body="talking about rpg stuff")
        response = self.client.get(self._detail_url(q="rpg"))
        self.assertNotContains(response, "Off-topic chat")

    def test_filter_pinned(self):
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Pinned one",
            slug="pinned-one",
            is_pinned=True,
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Plain one", slug="plain-one"
        )
        response = self.client.get(self._detail_url(filter="pinned"))
        self.assertContains(response, "Pinned one")
        self.assertNotContains(response, "Plain one")

    def test_filter_locked(self):
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Locked one",
            slug="locked-one",
            is_locked=True,
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Open one", slug="open-one"
        )
        response = self.client.get(self._detail_url(filter="locked"))
        self.assertContains(response, "Locked one")
        self.assertNotContains(response, "Open one")

    def test_filter_and_search_combine(self):
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Zelda thread",
            slug="zelda-thread",
            is_pinned=True,
        )
        Thread.objects.create(
            category=self.category,
            author=self.user,
            title="Mario thread",
            slug="mario-thread",
            is_pinned=True,
        )
        Thread.objects.create(
            category=self.category, author=self.user, title="Zelda info", slug="zelda-info"
        )
        response = self.client.get(self._detail_url(q="zelda", filter="pinned"))
        self.assertContains(response, "Zelda thread")
        self.assertNotContains(response, "Mario thread")
        self.assertNotContains(response, "Zelda info")

    def test_category_pagination_preserves_query_params(self):
        for i in range(THREADS_PER_PAGE + 5):
            Thread.objects.create(
                category=self.category,
                author=self.user,
                title=f"Term thread {i:02d}",
                slug=f"term-thread-{i:02d}",
            )
        response = self.client.get(self._detail_url(q="term", page="2"))
        self.assertContains(response, "q=term")


class ThreadDetailPaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.user, title="Long thread", slug="long-thread"
        )
        for i in range(POSTS_PER_PAGE + 5):
            Post.objects.create(thread=self.thread, author=self.user, body=f"Post body {i:02d}")
        self.client.login(username="player", password="password123")

    def _thread_url(self, **params):
        url = reverse("forum:thread_detail", args=[self.category.slug, self.thread.slug])
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def test_thread_detail_paginates_posts(self):
        response = self.client.get(self._thread_url())
        self.assertEqual(response.context["posts"].paginator.num_pages, 2)
        self.assertEqual(len(response.context["posts"].object_list), POSTS_PER_PAGE)

        response = self.client.get(self._thread_url(page="2"))
        self.assertEqual(len(response.context["posts"].object_list), 5)

    def test_thread_detail_page_2_does_not_break_reply_form(self):
        response = self.client.get(self._thread_url(page="2"))
        self.assertIsNotNone(response.context["reply_form"])
        self.assertContains(response, "Reply")
