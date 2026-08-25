from django.contrib import admin

from .models import KarmaEvent, KarmaScore


@admin.register(KarmaEvent)
class KarmaEventAdmin(admin.ModelAdmin):
    list_display = ["user", "points", "reason", "created_at"]
    list_filter = ["reason", "created_at"]
    search_fields = ["user__username"]


@admin.register(KarmaScore)
class KarmaScoreAdmin(admin.ModelAdmin):
    list_display = ["user", "score", "updated_at"]
    search_fields = ["user__username"]
