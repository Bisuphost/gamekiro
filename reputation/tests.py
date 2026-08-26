from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.template import Context, Template
from django.test import TestCase

from forum.models import Category, Post, Thread
from gamification.models import Badge, UserBadge
from reactions.models import Reaction

from .models import KarmaEvent, KarmaScore
from .services import recalculate_karma
from .tasks import recalculate_karma_task


class RecalculateKarmaTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="password123")
        self.liker = User.objects.create_user(username="liker", password="password123")
        self.category = Category.objects.create(name="General", slug="general")

    def test_score_from_threads_created_only(self):
        Thread.objects.create(category=self.category, author=self.author, title="One", slug="one")
        Thread.objects.create(category=self.category, author=self.author, title="Two", slug="two")
        score = recalculate_karma(self.author.pk)
        self.assertEqual(score, 2)

    def test_score_from_likes_on_thread_and_post(self):
        thread = Thread.objects.create(
            category=self.category, author=self.author, title="One", slug="one"
        )
        post = Post.objects.create(thread=thread, author=self.author, body="hi")
        Reaction.objects.create(
            user=self.liker,
            content_type=ContentType.objects.get_for_model(thread),
            object_id=thread.pk,
        )
        Reaction.objects.create(
            user=self.liker,
            content_type=ContentType.objects.get_for_model(post),
            object_id=post.pk,
        )
        score = recalculate_karma(self.author.pk)
        self.assertEqual(score, 3 + 2 + 1)

    def test_recalculation_is_idempotent(self):
        Thread.objects.create(category=self.category, author=self.author, title="One", slug="one")
        first = recalculate_karma(self.author.pk)
        second = recalculate_karma(self.author.pk)
        self.assertEqual(first, second)
        self.assertEqual(KarmaScore.objects.filter(user=self.author).count(), 1)


class ReactionSignalTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="password123")
        self.liker = User.objects.create_user(username="liker", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.author, title="One", slug="one"
        )
        self.post = Post.objects.create(thread=self.thread, author=self.author, body="hi")
        self.post_ct = ContentType.objects.get_for_model(self.post)
        self.thread_ct = ContentType.objects.get_for_model(self.thread)

    @patch("reputation.signals.recalculate_karma_task.delay")
    def test_liking_a_post_creates_karma_event_for_the_author(self, mock_delay):
        Reaction.objects.create(user=self.liker, content_type=self.post_ct, object_id=self.post.pk)
        event = KarmaEvent.objects.get()
        self.assertEqual(event.user, self.author)
        self.assertEqual(event.points, 2)
        self.assertEqual(event.reason, KarmaEvent.Reason.LIKE_RECEIVED)
        mock_delay.assert_called_once_with(self.author.pk)

    @patch("reputation.signals.recalculate_karma_task.delay")
    def test_liking_a_thread_creates_karma_event_for_the_author(self, mock_delay):
        Reaction.objects.create(
            user=self.liker, content_type=self.thread_ct, object_id=self.thread.pk
        )
        event = KarmaEvent.objects.get()
        self.assertEqual(event.points, 3)
        mock_delay.assert_called_once_with(self.author.pk)

    @patch("reputation.signals.recalculate_karma_task.delay")
    def test_unliking_creates_a_negative_karma_event(self, mock_delay):
        reaction = Reaction.objects.create(
            user=self.liker, content_type=self.post_ct, object_id=self.post.pk
        )
        mock_delay.reset_mock()
        reaction.delete()
        removal_event = KarmaEvent.objects.get(reason=KarmaEvent.Reason.LIKE_REMOVED)
        self.assertEqual(removal_event.points, -2)
        mock_delay.assert_called_once_with(self.author.pk)

    @patch("reputation.signals.recalculate_karma_task.delay")
    def test_self_like_still_records_event(self, mock_delay):
        Reaction.objects.create(user=self.author, content_type=self.post_ct, object_id=self.post.pk)
        self.assertEqual(KarmaEvent.objects.count(), 1)


class RecalculateKarmaTaskBadgeIntegrationTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="password123")
        self.karma_badge = Badge.objects.get(criteria_key=Badge.CriteriaKey.KARMA_50)

    def test_recalculate_karma_task_awards_badge_after_recalculating_score(self):
        category = Category.objects.create(name="General", slug="general")
        for i in range(50):
            Thread.objects.create(
                category=category, author=self.author, title=f"T{i}", slug=f"t{i}"
            )
        recalculate_karma_task(self.author.pk)
        self.assertEqual(KarmaScore.objects.get(user=self.author).score, 50)
        self.assertTrue(UserBadge.objects.filter(user=self.author, badge=self.karma_badge).exists())

    def test_recalculate_karma_task_is_idempotent_across_repeated_calls(self):
        category = Category.objects.create(name="General", slug="general")
        for i in range(50):
            Thread.objects.create(
                category=category, author=self.author, title=f"T{i}", slug=f"t{i}"
            )
        recalculate_karma_task(self.author.pk)
        recalculate_karma_task(self.author.pk)
        self.assertEqual(
            UserBadge.objects.filter(user=self.author, badge=self.karma_badge).count(), 1
        )


class KarmaBadgeTemplateTagTests(TestCase):
    def test_karma_badge_renders_zero_for_user_without_score(self):
        user = User.objects.create_user(username="nobody", password="password123")
        template = Template("{% load reputation_tags %}{% karma_badge user %}")
        rendered = template.render(Context({"user": user}))
        self.assertIn("0", rendered)
