from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from taggit.managers import TaggableManager


class Platform(models.Model):
	name = models.CharField(max_length=100, unique=True)
	slug = models.SlugField(max_length=100, unique=True)

	class Meta:
		ordering = ["name"]

	def __str__(self):
		return self.name


class Game(models.Model):
	title = models.CharField(max_length=200)
	slug = models.SlugField(max_length=200, unique=True)
	description = models.TextField(blank=True)
	cover_image = models.ImageField(upload_to="game_covers/", blank=True, null=True)
	platforms = models.ManyToManyField(Platform, related_name="games", blank=True)
	tags = TaggableManager(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["title"]

	def __str__(self):
		return self.title


class Review(models.Model):
	game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="reviews")
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_reviews")
	rating = models.PositiveSmallIntegerField(
		validators=[MinValueValidator(1), MaxValueValidator(5)],
	)
	body = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-created_at"]
		constraints = [
			models.UniqueConstraint(fields=["user", "game"], name="unique_review_per_user_game")
		]

	def __str__(self):
		return f"{self.user} reviewed {self.game} ({self.rating}/5)"
