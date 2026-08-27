from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from forum.models import Category, Post, Thread
from gamification.models import Badge
from messaging.models import Message
from reactions.models import Reaction
from reputation.tasks import recalculate_karma_task
from social.models import Follow

from .models import Notification
from .services import notify


class NotifyTests(TestCase):
    def setUp(self):
        self.recipient = User.objects.create_user(username="recipient", password="password123")
        self.actor = User.objects.create_user(username="actor", password="password123")

    def test_creates_notification_with_correct_fields(self):
        notification = notify(
            recipient=self.recipient, actor=self.actor, verb=Notification.Verb.NEW_FOLLOWER
        )
        self.assertEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.actor, self.actor)
        self.assertEqual(notification.verb, Notification.Verb.NEW_FOLLOWER)

    def test_self_notification_is_a_no_op(self):
        result = notify(
            recipient=self.recipient, actor=self.recipient, verb=Notification.Verb.NEW_FOLLOWER
        )
        self.assertIsNone(result)
        self.assertEqual(Notification.objects.count(), 0)

    def test_nullable_actor_is_allowed(self):
        notification = notify(
            recipient=self.recipient, actor=None, verb=Notification.Verb.BADGE_EARNED
        )
        self.assertIsNone(notification.actor)


class NewFollowerNotificationTests(TestCase):
    def setUp(self):
        self.follower = User.objects.create_user(username="follower", password="password123")
        self.followed = User.objects.create_user(username="followed", password="password123")
        self.client.login(username="follower", password="password123")

    def test_following_creates_a_notification(self):
        self.client.post(reverse("social:toggle-follow", args=["followed"]))
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient, self.followed)
        self.assertEqual(notification.actor, self.follower)
        self.assertEqual(notification.verb, Notification.Verb.NEW_FOLLOWER)

    def test_unfollowing_creates_no_additional_notification(self):
        url = reverse("social:toggle-follow", args=["followed"])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(Notification.objects.count(), 1)


class ThreadReplyNotificationTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="password123")
        self.replier = User.objects.create_user(username="replier", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.author, title="T", slug="t"
        )

    def test_reply_notifies_thread_author(self):
        self.client.login(username="replier", password="password123")
        self.client.post(reverse("forum:post_create", args=["general", "t"]), {"body": "hello"})
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient, self.author)
        self.assertEqual(notification.verb, Notification.Verb.THREAD_REPLY)
        self.assertIsInstance(notification.target, Post)

    def test_self_reply_creates_no_notification(self):
        self.client.login(username="author", password="password123")
        self.client.post(reverse("forum:post_create", args=["general", "t"]), {"body": "hello"})
        self.assertEqual(Notification.objects.count(), 0)


class DMReceivedNotificationTests(TestCase):
    def setUp(self):
        self.sender = User.objects.create_user(username="sender", password="password123")
        self.recipient = User.objects.create_user(username="recipient", password="password123")
        self.client.login(username="sender", password="password123")

    def test_sending_a_message_notifies_the_recipient(self):
        self.client.post(reverse("messaging:send", args=["recipient"]), {"body": "hi there"})
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient, self.recipient)
        self.assertEqual(notification.actor, self.sender)
        self.assertEqual(notification.verb, Notification.Verb.DM_RECEIVED)
        self.assertIsInstance(notification.target, Message)


class ReactionReceivedNotificationTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="password123")
        self.liker = User.objects.create_user(username="liker", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        self.thread = Thread.objects.create(
            category=self.category, author=self.author, title="T", slug="t"
        )
        self.post = Post.objects.create(thread=self.thread, author=self.author, body="hi")

    def _react(self, user):
        return Reaction.objects.create(
            user=user,
            content_type=ContentType.objects.get_for_model(self.post),
            object_id=self.post.pk,
        )

    @patch("reputation.signals.recalculate_karma_task.delay")
    def test_liking_notifies_the_author(self, mock_delay):
        self._react(self.liker)
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient, self.author)
        self.assertEqual(notification.actor, self.liker)
        self.assertEqual(notification.verb, Notification.Verb.REACTION_RECEIVED)

    @patch("reputation.signals.recalculate_karma_task.delay")
    def test_unliking_creates_no_additional_notification(self, mock_delay):
        reaction = self._react(self.liker)
        reaction.delete()
        self.assertEqual(Notification.objects.count(), 1)

    @patch("reputation.signals.recalculate_karma_task.delay")
    def test_self_reaction_creates_no_notification(self, mock_delay):
        self._react(self.author)
        self.assertEqual(Notification.objects.count(), 0)


class BadgeEarnedNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="earner", password="password123")
        self.category = Category.objects.create(name="General", slug="general")
        Thread.objects.create(category=self.category, author=self.user, title="T", slug="t")

    def test_earning_a_badge_notifies_with_no_actor(self):
        recalculate_karma_task(self.user.pk)
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient, self.user)
        self.assertIsNone(notification.actor)
        self.assertEqual(notification.verb, Notification.Verb.BADGE_EARNED)
        self.assertIsInstance(notification.target, Badge)

    def test_calling_again_creates_no_duplicate_notification(self):
        recalculate_karma_task(self.user.pk)
        recalculate_karma_task(self.user.pk)
        self.assertEqual(Notification.objects.count(), 1)


class NotificationListSecurityTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username="user_a", password="password123")
        self.user_b = User.objects.create_user(username="user_b", password="password123")
        self.notification_for_b = notify(
            recipient=self.user_b, actor=self.user_a, verb=Notification.Verb.NEW_FOLLOWER
        )

    def test_user_cannot_see_another_users_notifications(self):
        self.client.login(username="user_a", password="password123")
        response = self.client.get(reverse("notifications:list"))
        self.assertNotContains(response, "started following")

    def test_user_cannot_mark_read_another_users_notification(self):
        self.client.login(username="user_a", password="password123")
        url = reverse("notifications:mark-read", args=[self.notification_for_b.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.notification_for_b.refresh_from_db()
        self.assertFalse(self.notification_for_b.is_read)

    def test_mark_read_preserves_the_page_number(self):
        self.client.login(username="user_b", password="password123")
        url = reverse("notifications:mark-read", args=[self.notification_for_b.pk])
        response = self.client.post(url, {"page": "2"})
        self.assertRedirects(response, reverse("notifications:list") + "?page=2")

    def test_mark_read_ignores_a_non_numeric_page_value(self):
        self.client.login(username="user_b", password="password123")
        url = reverse("notifications:mark-read", args=[self.notification_for_b.pk])
        response = self.client.post(url, {"page": "not-a-number"})
        self.assertRedirects(response, reverse("notifications:list"))

    def test_owner_can_mark_their_own_notification_read(self):
        self.client.login(username="user_b", password="password123")
        url = reverse("notifications:mark-read", args=[self.notification_for_b.pk])
        self.client.post(url)
        self.notification_for_b.refresh_from_db()
        self.assertTrue(self.notification_for_b.is_read)


class NotificationListPaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.other = User.objects.create_user(username="other", password="password123")
        for _ in range(25):
            notify(recipient=self.user, actor=self.other, verb=Notification.Verb.NEW_FOLLOWER)
        self.client.login(username="player", password="password123")

    def test_list_paginates(self):
        response = self.client.get(reverse("notifications:list"))
        self.assertEqual(response.context["notifications"].paginator.num_pages, 2)


class UnreadCountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="player", password="password123")
        self.other = User.objects.create_user(username="other", password="password123")
        self.client.login(username="player", password="password123")

    def test_zero_unread_renders_empty(self):
        response = self.client.get(reverse("notifications:unread-count"))
        self.assertNotContains(response, "bg-red-600")

    def test_unread_count_reflects_actual_count_and_drops_after_mark_read(self):
        notification = notify(
            recipient=self.user, actor=self.other, verb=Notification.Verb.NEW_FOLLOWER
        )
        response = self.client.get(reverse("notifications:unread-count"))
        self.assertContains(response, ">1<")

        self.client.post(reverse("notifications:mark-read", args=[notification.pk]))
        response = self.client.get(reverse("notifications:unread-count"))
        self.assertNotContains(response, "bg-red-600")


class FollowModelTests(TestCase):
    def test_follow_still_enforces_constraints_after_field_dedupe(self):
        follower = User.objects.create_user(username="a", password="password123")
        following = User.objects.create_user(username="b", password="password123")
        follow = Follow.objects.create(follower=follower, following=following)
        self.assertEqual(follow.follower, follower)
        self.assertEqual(follow.following, following)
