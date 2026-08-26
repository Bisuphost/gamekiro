from django.contrib import admin

from .models import Badge, UserBadge


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ["name", "criteria_key", "is_active"]
    list_filter = ["is_active", "criteria_key"]
    search_fields = ["name"]


@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
    list_display = ["user", "badge", "awarded_at"]
    list_filter = ["badge", "awarded_at"]
    search_fields = ["user__username", "badge__name"]
