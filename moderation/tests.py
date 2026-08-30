from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from forum.models import Category, Post, Thread

from .models import Report
from .services import hidden_object_ids


class ReportViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.other_user = User.objects.create_user(username="rival", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.user, title="Hello", slug="hello"
        )
        self.post = Post.objects.create(thread=self.thread, author=self.user, body="First post")
        self.client.login(username="rival", password="password123")

    def _report_url(self, app_label, model_name, pk):
        return reverse("moderation:report", args=[app_label, model_name, pk])

    def test_report_creates_report(self):
        response = self.client.post(
            self._report_url("forum", "post", self.post.pk), {"reason": Report.Reason.SPAM}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Report.objects.count(), 1)
        report = Report.objects.get()
        self.assertEqual(report.reporter, self.other_user)
        self.assertEqual(report.target, self.post)

    def test_duplicate_report_by_same_user_is_noop(self):
        url = self._report_url("forum", "post", self.post.pk)
        self.client.post(url, {"reason": Report.Reason.SPAM})
        self.client.post(url, {"reason": Report.Reason.HARASSMENT})
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(Report.objects.get().reason, Report.Reason.SPAM)

    def test_database_rejects_duplicate_report(self):
        content_type = ContentType.objects.get_for_model(Post)
        Report.objects.create(
            reporter=self.other_user,
            content_type=content_type,
            object_id=self.post.pk,
            reason=Report.Reason.SPAM,
        )
        with self.assertRaises(IntegrityError):
            Report.objects.create(
                reporter=self.other_user,
                content_type=content_type,
                object_id=self.post.pk,
                reason=Report.Reason.OTHER,
            )

    def test_report_requires_login(self):
        self.client.logout()
        response = self.client.post(
            self._report_url("forum", "post", self.post.pk), {"reason": Report.Reason.SPAM}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_report_requires_post(self):
        response = self.client.get(self._report_url("forum", "post", self.post.pk))
        self.assertEqual(response.status_code, 405)

    def test_report_rejects_non_allowlisted_model(self):
        response = self.client.post(
            self._report_url("forum", "thread", self.thread.pk), {"reason": Report.Reason.SPAM}
        )
        self.assertEqual(response.status_code, 404)

    def test_report_rejects_nonexistent_model(self):
        response = self.client.post(
            self._report_url("nope", "nope", 1), {"reason": Report.Reason.SPAM}
        )
        self.assertEqual(response.status_code, 404)

    def test_report_rejects_unsafe_redirect(self):
        response = self.client.post(
            self._report_url("forum", "post", self.post.pk),
            {"reason": Report.Reason.SPAM, "next": "https://evil.example.com/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("evil.example.com", response.url)

    def test_report_allows_safe_next(self):
        target = reverse("forum:thread_detail", args=[self.category.slug, self.thread.slug])
        response = self.client.post(
            self._report_url("forum", "post", self.post.pk),
            {"reason": Report.Reason.SPAM, "next": target},
        )
        self.assertRedirects(response, target)


class HiddenObjectIdsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.user, title="Hello", slug="hello"
        )
        self.post = Post.objects.create(thread=self.thread, author=self.user, body="First post")

    def test_returns_empty_with_no_reports(self):
        self.assertEqual(list(hidden_object_ids(Post)), [])

    def test_returns_ids_only_when_hides_target_true(self):
        content_type = ContentType.objects.get_for_model(Post)
        Report.objects.create(
            reporter=self.user,
            content_type=content_type,
            object_id=self.post.pk,
            reason=Report.Reason.SPAM,
            hides_target=False,
        )
        self.assertEqual(list(hidden_object_ids(Post)), [])

        Report.objects.filter(pk=Report.objects.get().pk).update(hides_target=True)
        self.assertEqual(list(hidden_object_ids(Post)), [self.post.pk])

    def test_scoped_to_correct_content_type(self):
        content_type = ContentType.objects.get_for_model(Post)
        Report.objects.create(
            reporter=self.user,
            content_type=content_type,
            object_id=self.post.pk,
            reason=Report.Reason.SPAM,
            hides_target=True,
        )
        self.assertEqual(list(hidden_object_ids(Thread)), [])
