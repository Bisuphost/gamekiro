from django.db import models
from django.utils.text import slugify

from core.models import TimestampedModel
from core.validators import validate_image_file_size
from games.models import Game


class ActivationPlatform(TimestampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=110, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Region(TimestampedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft"
        ACTIVE = "active"
        UNLISTED = "unlisted"
        ARCHIVED = "archived"

    game = models.ForeignKey(Game, on_delete=models.PROTECT, related_name="marketplace_products")
    slug = models.SlugField(max_length=220, unique=True)
    activation_platform = models.ForeignKey(
        ActivationPlatform, on_delete=models.PROTECT, related_name="products"
    )
    region = models.ForeignKey(Region, on_delete=models.PROTECT, related_name="products")
    edition = models.CharField(max_length=100, blank=True)
    unit_price_minor = models.PositiveBigIntegerField()
    compare_at_price_minor = models.PositiveBigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    is_purchasable = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    max_per_order = models.PositiveSmallIntegerField(default=10)
    publisher = models.CharField(max_length=150, blank=True)
    developer = models.CharField(max_length=150, blank=True)
    released_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["game", "activation_platform", "region", "edition"],
                name="uniq_product_game_platform_region_edition",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "is_purchasable"], name="product_status_purchasable_idx"
            ),
        ]

    def __str__(self):
        return f"{self.game.title} ({self.activation_platform.name}, {self.region.code})"

    @property
    def is_on_sale(self):
        return (
            self.compare_at_price_minor is not None
            and self.compare_at_price_minor > self.unit_price_minor
        )


class ProductImage(TimestampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(
        upload_to="marketplace_products/", validators=[validate_image_file_size]
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"Image for {self.product}"
