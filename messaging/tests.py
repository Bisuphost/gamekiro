from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import ConversationArchive, Message
from .services import RATE_LIMIT_MAX


class MessagingTests(TestCase):
    def setUp(self):
        self.sender = User.objects.create_user(username="sender", password="password123")
        self.recipient = User.objects.create_user(username="recipient", password="password123")
        self.third_user = User.objects.create_user(username="third", password="password123")

    def test_authenticated_user_can_send_message(self):
        self.client.login(username="sender", password="password123")

        response = self.client.post(
            reverse("messaging:send", args=[self.recipient.username]),
            {"body": "Good game!"},
        )

        self.assertRedirects(response, reverse("messaging:conversation", args=["recipient"]))
        message = Message.objects.get()
        self.assertEqual(message.sender, self.sender)
        self.assertEqual(message.recipient, self.recipient)
        self.assertFalse(message.is_read)

    def test_conversation_is_private_and_marks_received_messages_read(self):
        message = Message.objects.create(
            sender=self.sender,
            recipient=self.recipient,
            body="Private hello",
        )
        self.client.login(username="recipient", password="password123")

        response = self.client.get(reverse("messaging:conversation", args=["sender"]))

        self.assertContains(response, "Private hello")
        message.refresh_from_db()
        self.assertTrue(message.is_read)
        self.assertNotContains(
            self.client.get(reverse("messaging:conversation", args=["third"])),
            "Private hello",
        )

    def test_inbox_groups_conversations_and_counts_unread_messages(self):
        Message.objects.create(sender=self.sender, recipient=self.recipient, body="First")
        Message.objects.create(sender=self.recipient, recipient=self.sender, body="Reply")
        Message.objects.create(sender=self.sender, recipient=self.recipient, body="Latest")
        self.client.login(username="recipient", password="password123")

        response = self.client.get(reverse("messaging:inbox"))

        self.assertContains(response, "Latest")
        self.assertContains(response, "2 unread")
        self.assertEqual(response.context["conversations"].__len__(), 1)

    def test_unread_count_only_includes_current_users_received_messages(self):
        Message.objects.create(sender=self.sender, recipient=self.recipient, body="One")
        Message.objects.create(sender=self.third_user, recipient=self.recipient, body="Two")
        Message.objects.create(sender=self.recipient, recipient=self.sender, body="Sent")
        self.client.login(username="recipient", password="password123")

        response = self.client.get(reverse("messaging:unread-count"))

        self.assertContains(response, ">2<")

    def test_message_views_require_authentication(self):
        response = self.client.get(reverse("messaging:inbox"))
        self.assertRedirects(response, "/accounts/login/?next=/messages/")

    def test_get_send_endpoint_does_not_send_a_message(self):
        self.client.login(username="sender", password="password123")

        response = self.client.get(reverse("messaging:send", args=["recipient"]))

        self.assertRedirects(response, reverse("messaging:conversation", args=["recipient"]))
        self.assertFalse(Message.objects.exists())

    def test_user_list_excludes_current_user_and_supports_search(self):
        self.sender.first_name = "Sam"
        self.sender.save(update_fields=["first_name"])
        self.client.login(username="sender", password="password123")

        response = self.client.get(reverse("messaging:user-list"), {"q": "sam"})

        self.assertEqual(list(response.context["users"]), [])

        response = self.client.get(reverse("messaging:user-list"), {"q": "rec"})
        self.assertContains(response, "recipient")
        self.assertEqual(list(response.context["users"]), [self.recipient])

    def test_user_cannot_message_themselves(self):
        self.client.login(username="sender", password="password123")

        response = self.client.post(
            reverse("messaging:send", args=["sender"]),
            {"body": "This should not be sent"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Message.objects.exists())

    def test_user_can_archive_only_their_own_conversation(self):
        Message.objects.create(sender=self.sender, recipient=self.recipient, body="Keep this")
        self.client.login(username="recipient", password="password123")

        response = self.client.post(reverse("messaging:archive", args=["sender"]))

        self.assertRedirects(response, reverse("messaging:inbox"))
        self.assertTrue(
            ConversationArchive.objects.filter(user=self.recipient, partner=self.sender).exists()
        )
        self.assertNotContains(self.client.get(reverse("messaging:inbox")), "Keep this")

        self.client.login(username="sender", password="password123")
        self.client.post(reverse("messaging:archive", args=["recipient"]))
        self.assertTrue(
            ConversationArchive.objects.filter(user=self.sender, partner=self.recipient).exists()
        )

    def test_sending_too_many_messages_is_rate_limited(self):
        self.client.login(username="sender", password="password123")
        url = reverse("messaging:send", args=["recipient"])

        for i in range(RATE_LIMIT_MAX):
            self.client.post(url, {"body": f"Message {i}"})
        self.assertEqual(Message.objects.count(), RATE_LIMIT_MAX)

        response = self.client.post(url, {"body": "One too many"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Message.objects.count(), RATE_LIMIT_MAX)

    def test_new_message_after_archive_is_visible_and_unread(self):
        Message.objects.create(sender=self.sender, recipient=self.recipient, body="Old")
        self.client.login(username="recipient", password="password123")
        self.client.post(reverse("messaging:archive", args=["sender"]))

        self.client.login(username="sender", password="password123")
        self.client.post(reverse("messaging:send", args=["recipient"]), {"body": "New"})
        self.client.login(username="recipient", password="password123")
        response = self.client.get(reverse("messaging:inbox"))

        self.assertContains(response, "New")
        self.assertContains(response, "1 unread")
