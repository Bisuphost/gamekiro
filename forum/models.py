from django.conf import settings
from django.db import connection, models

from core.validators import validate_image_file_size

# Full-text search (title_search_vector / GinIndex) is a PostgreSQL-only
# feature. It's defined conditionally so the app still runs against other
# backends (e.g. SQLite, used as a fallback where PostgreSQL 14+ isn't
# available) — full-text search then falls back to a plain icontains filter
# in forum/views.py.
POSTGRES = connection.vendor == "postgresql"

if POSTGRES:
    from django.contrib.postgres.indexes import GinIndex
    from django.contrib.postgres.search import SearchVector, SearchVectorField


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Thread(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="threads")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="threads"
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    is_pinned = models.BooleanField(default=False)
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    if POSTGRES:
        title_search_vector = models.GeneratedField(
            expression=SearchVector("title", config="english"),
            output_field=SearchVectorField(),
            db_persist=True,
        )

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "slug"], name="unique_thread_slug_per_category"
            )
        ]
        indexes = (
            [GinIndex(fields=["title_search_vector"], name="thread_title_search_idx")]
            if POSTGRES
            else []
        )

    def __str__(self):
        return self.title


class Post(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="forum_posts"
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author} on {self.thread}"


class PostImage(models.Model):
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="forum_post_images"
    )
    image = models.ImageField(upload_to="post_images/", validators=[validate_image_file_size])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"image uploaded by {self.uploader}"
