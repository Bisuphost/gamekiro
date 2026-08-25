from django.test import TestCase

# Create your tests here.
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from .models import Follow


class FollowTests(TestCase):
    def setUp(self):
        self.follower = User.objects.create_user(username="player", password="password123")
        self.following = User.objects.create_user(username="rival", password="password123")
        self.client.login(username="player", password="password123")

    def test_follow_toggle_creates_and_deletes_follow(self):
        url = reverse("social:toggle-follow", args=["rival"])
        response = self.client.post(url)
        self.assertRedirects(response, reverse("accounts:profile-detail", args=["rival"]))
        self.assertTrue(Follow.objects.filter(follower=self.follower, following=self.following).exists())

        self.client.post(url)
        self.assertFalse(Follow.objects.filter(follower=self.follower, following=self.following).exists())

    def test_user_cannot_follow_themselves(self):
        self.client.post(reverse("social:toggle-follow", args=["player"]))
        self.assertFalse(Follow.objects.exists())

    def test_database_rejects_self_follow(self):
        with self.assertRaises(IntegrityError):
            Follow.objects.create(follower=self.follower, following=self.follower)
