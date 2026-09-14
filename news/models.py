from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimestampedModel
from core.validators import validate_image_file_size


class Article(TimestampedModel):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    summary = models.CharField(max_length=300, blank=True)
    body = models.TextField()
    cover_image = models.ImageField(
        upload_to="news_covers/", blank=True, null=True, validators=[validate_image_file_size]
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="news_articles",
    )
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title

    @property
    def is_visible(self):
        return self.is_published and self.published_at <= timezone.now()
