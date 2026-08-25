from django.contrib import admin

from .models import Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
	list_display = ("sender", "recipient", "is_read", "created_at")
	list_filter = ("is_read", "created_at")
	search_fields = ("sender__username", "recipient__username", "body")
	readonly_fields = ("created_at",)
