from django.contrib import admin

from .models import Category, Post, Thread


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "order"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ["title", "category", "author", "is_pinned", "is_locked", "created_at"]
    list_filter = ["category", "is_pinned", "is_locked"]
    list_editable = ["is_pinned", "is_locked"]
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["thread", "author", "created_at"]
