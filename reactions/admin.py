from django.contrib import admin

from .models import Reaction


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ["user", "content_type", "object_id", "created_at"]
    list_filter = ["content_type", "created_at"]
    search_fields = ["user__username"]
