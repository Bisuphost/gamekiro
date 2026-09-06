from django.contrib import admin

from .models import ConversationArchive, Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
	list_display = ("sender", "recipient", "is_read", "created_at")
	list_filter = ("is_read", "created_at")
	search_fields = ("sender__username", "recipient__username", "body")
	readonly_fields = ("created_at",)


@admin.register(ConversationArchive)
class ConversationArchiveAdmin(admin.ModelAdmin):
	list_display = ("user", "partner", "created_at")
	search_fields = ("user__username", "partner__username")
