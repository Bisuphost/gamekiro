from django.conf import settings
from django.db import models

from core.models import TimestampedModel
from games.models import Game


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


class ProfileGame(TimestampedModel):
    PLAYING = "playing"
    COMPLETED = "completed"
    WISHLIST = "wishlist"
    DROPPED = "dropped"
    STATUS_CHOICES = [
        (PLAYING, "Playing"),
        (COMPLETED, "Completed"),
        (WISHLIST, "Wishlist"),
        (DROPPED, "Dropped"),
    ]

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="profile_games")
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="profile_games")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PLAYING)

    class Meta:
        ordering = ["status", "game__title"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "game"], name="unique_profile_game")
        ]
        indexes = [models.Index(fields=["profile", "status"])]

    def __str__(self):
        return f"{self.profile} - {self.game}"
