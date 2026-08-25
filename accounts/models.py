from django.conf import settings
from django.db import models

from core.models import TimestampedModel


class Profile(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    discord_username = models.CharField(max_length=64, blank=True)
    steam_id = models.CharField(max_length=64, blank=True)
    psn_id = models.CharField(max_length=64, blank=True)
    website = models.URLField(blank=True)
    other_links = models.URLField(blank=True)

    def __str__(self):
        return self.user.username
