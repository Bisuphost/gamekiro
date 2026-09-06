from django.contrib import admin

from .models import Game, Platform, Review


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
	prepopulated_fields = {"slug": ("name",)}
	search_fields = ("name",)


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
	list_display = ("title", "slug")
	list_filter = ("platforms",)
	prepopulated_fields = {"slug": ("title",)}
	search_fields = ("title", "description")
	filter_horizontal = ("platforms",)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
	list_display = ("game", "user", "rating", "created_at")
	list_filter = ("game", "rating")
	search_fields = ("user__username", "game__title", "body")
