from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
	bio = models.TextField(blank=True)
	avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
	discord_username = models.CharField(max_length=100, blank=True)
	steam_id = models.CharField(max_length=100, blank=True)
	psn_id = models.CharField(max_length=100, blank=True)
	other_links = models.URLField(blank=True)

	def __str__(self):
		return f"{self.user.username}'s profile"
