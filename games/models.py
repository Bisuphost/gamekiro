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
