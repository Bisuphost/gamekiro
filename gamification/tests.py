from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase

from forum.models import Category, Post, Thread
from reputation.models import KarmaScore

from .models import Badge, UserBadge
from .services import check_badges


class CheckBadgesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.karma_badge = Badge.objects.get(criteria_key=Badge.CriteriaKey.KARMA_50)
        self.posts_badge = Badge.objects.get(criteria_key=Badge.CriteriaKey.POSTS_10)
        self.thread_badge = Badge.objects.get(criteria_key=Badge.CriteriaKey.FIRST_THREAD)

    def test_awards_karma_badge_when_score_crosses_threshold(self):
        KarmaScore.objects.create(user=self.user, score=50)
        check_badges(self.user.pk)
        self.assertTrue(UserBadge.objects.filter(user=self.user, badge=self.karma_badge).exists())

    def test_does_not_award_when_score_below_threshold(self):
        KarmaScore.objects.create(user=self.user, score=49)
        check_badges(self.user.pk)
        self.assertEqual(UserBadge.objects.count(), 0)

    def test_awards_posts_10_badge(self):
        thread = Thread.objects.create(
            category=self.category, author=self.user, title="T", slug="t"
        )
        for i in range(10):
            Post.objects.create(thread=thread, author=self.user, body=f"post {i}")
        check_badges(self.user.pk)
        self.assertTrue(UserBadge.objects.filter(user=self.user, badge=self.posts_badge).exists())

    def test_awards_first_thread_badge(self):
        Thread.objects.create(category=self.category, author=self.user, title="T", slug="t")
        check_badges(self.user.pk)
        self.assertTrue(UserBadge.objects.filter(user=self.user, badge=self.thread_badge).exists())

    def test_is_idempotent_no_duplicate_award(self):
        Thread.objects.create(category=self.category, author=self.user, title="T", slug="t")
        check_badges(self.user.pk)
        check_badges(self.user.pk)
        self.assertEqual(UserBadge.objects.filter(user=self.user).count(), 1)

    def test_inactive_badge_is_never_awarded(self):
        self.thread_badge.is_active = False
        self.thread_badge.save()
        Thread.objects.create(category=self.category, author=self.user, title="T", slug="t")
        check_badges(self.user.pk)
        self.assertFalse(UserBadge.objects.filter(user=self.user, badge=self.thread_badge).exists())

    def test_check_badges_returns_newly_awarded_badges_only(self):
        Thread.objects.create(category=self.category, author=self.user, title="T", slug="t")
        first_call = check_badges(self.user.pk)
        second_call = check_badges(self.user.pk)
        self.assertIn(self.thread_badge, first_call)
        self.assertNotIn(self.thread_badge, second_call)


class AchievementBadgesTemplateTagTests(TestCase):
    def test_renders_no_achievements_message_when_empty(self):
        user = User.objects.create_user(username="nobody", password="password123")
        template = Template("{% load gamification_tags %}{% achievement_badges user %}")
        rendered = template.render(Context({"user": user}))
        self.assertIn("No achievements yet.", rendered)

    def test_renders_badge_name_when_earned(self):
        user = User.objects.create_user(username="earner", password="password123")
        badge = Badge.objects.get(criteria_key=Badge.CriteriaKey.FIRST_THREAD)
        UserBadge.objects.create(user=user, badge=badge)
        template = Template("{% load gamification_tags %}{% achievement_badges user %}")
        rendered = template.render(Context({"user": user}))
        self.assertIn(badge.name, rendered)
