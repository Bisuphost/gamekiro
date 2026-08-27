from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Notification(models.Model):
    class Verb(models.TextChoices):
        NEW_FOLLOWER = "new_follower"
        THREAD_REPLY = "thread_reply"
        DM_RECEIVED = "dm_received"
        REACTION_RECEIVED = "reaction_received"
        BADGE_EARNED = "badge_earned"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    verb = models.CharField(max_length=20, choices=Verb.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "created_at"], name="notif_recipient_recent_idx"),
            models.Index(fields=["recipient", "is_read"], name="notif_recipient_unread_idx"),
        ]

    def __str__(self):
        return f"{self.recipient}: {self.verb}"
