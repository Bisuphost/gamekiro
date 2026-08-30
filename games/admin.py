from django.contrib import admin

from .models import Game, Platform


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
