from django.contrib import admin

from .models import Profile, ProfileGame


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "discord_username", "steam_id", "psn_id"]
    search_fields = ["user__username", "discord_username", "steam_id", "psn_id"]


@admin.register(ProfileGame)
class ProfileGameAdmin(admin.ModelAdmin):
    list_display = ["profile", "game", "status"]
    list_filter = ["status"]
    search_fields = ["profile__user__username", "game__title"]
