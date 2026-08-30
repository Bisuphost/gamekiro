from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Report(models.Model):
    class Reason(models.TextChoices):
        SPAM = "spam"
        HARASSMENT = "harassment"
        OFF_TOPIC = "off_topic"
        OTHER = "other"

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_filed"
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    detail = models.TextField(blank=True)
    is_resolved = models.BooleanField(default=False)
    hides_target = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "content_type", "object_id"],
                name="unique_report_per_user_per_target",
            )
        ]
        indexes = [
            models.Index(fields=["content_type", "object_id"], name="report_target_idx"),
        ]

    def __str__(self):
        return f"{self.reporter} reported {self.target}"
