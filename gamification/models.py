from django.conf import settings
from django.db import models


class Badge(models.Model):
    class CriteriaKey(models.TextChoices):
        KARMA_50 = "karma_50"
        POSTS_10 = "posts_10"
        FIRST_THREAD = "first_thread"

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True)
    criteria_key = models.CharField(max_length=30, choices=CriteriaKey.choices, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserBadge(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="badges"
    )
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="user_badges")
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-awarded_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "badge"], name="unique_user_badge"),
        ]

    def __str__(self):
        return f"{self.user}: {self.badge.name}"
