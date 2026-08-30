from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.models import TimestampedModel


class KarmaEvent(models.Model):
    class Reason(models.TextChoices):
        LIKE_RECEIVED = "like_received"
        LIKE_REMOVED = "like_removed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="karma_events"
    )
    points = models.IntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"], name="karma_event_user_recent_idx"),
        ]

    def __str__(self):
        return f"{self.user}: {self.points:+d} ({self.reason})"


class KarmaScore(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="karma_score"
    )
    score = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.user}: {self.score}"
