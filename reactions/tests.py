from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from forum.models import Category, Post, Thread

from .models import Reaction


class ReactionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.other_user = User.objects.create_user(username="rival", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.user, title="Hello", slug="hello"
        )
        self.post = Post.objects.create(thread=self.thread, author=self.user, body="First post")
        self.client.login(username="player", password="password123")

    def _toggle_url(self, app_label, model_name, pk):
        return reverse("reactions:toggle", args=[app_label, model_name, pk])

    def test_toggle_creates_reaction(self):
        response = self.client.post(self._toggle_url("forum", "post", self.post.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reaction.objects.count(), 1)

    def test_toggle_twice_removes_reaction(self):
        url = self._toggle_url("forum", "post", self.post.pk)
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(Reaction.objects.count(), 0)

    def test_toggle_thread_and_post_independent(self):
        self.client.post(self._toggle_url("forum", "thread", self.thread.pk))
        self.client.post(self._toggle_url("forum", "post", self.post.pk))
        self.assertEqual(Reaction.objects.count(), 2)

    def test_database_rejects_duplicate_reaction(self):
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(Post)
        Reaction.objects.create(user=self.user, content_type=content_type, object_id=self.post.pk)
        with self.assertRaises(IntegrityError):
            Reaction.objects.create(
                user=self.user, content_type=content_type, object_id=self.post.pk
            )

    def test_toggle_requires_login(self):
        self.client.logout()
        response = self.client.post(self._toggle_url("forum", "post", self.post.pk))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_toggle_requires_post(self):
        response = self.client.get(self._toggle_url("forum", "post", self.post.pk))
        self.assertEqual(response.status_code, 405)

    def test_toggle_rejects_non_allowlisted_model(self):
        response = self.client.post(self._toggle_url("accounts", "profile", 1))
        self.assertEqual(response.status_code, 404)

    def test_toggle_rejects_nonexistent_model(self):
        response = self.client.post(self._toggle_url("nope", "nope", 1))
        self.assertEqual(response.status_code, 404)

    def test_htmx_request_returns_partial_not_redirect(self):
        response = self.client.post(
            self._toggle_url("forum", "post", self.post.pk), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1")

    def test_count_correct_across_multiple_users(self):
        url = self._toggle_url("forum", "post", self.post.pk)
        self.client.post(url)
        self.client.login(username="rival", password="password123")
        self.client.post(url)
        self.assertEqual(Reaction.objects.count(), 2)
