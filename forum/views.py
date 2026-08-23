from django.shortcuts import get_object_or_404, render

from .models import Category, Thread


def category_list(request):
    categories = Category.objects.all()
    return render(request, "forum/category_list.html", {"categories": categories})


def category_detail(request, category_slug):
    category = get_object_or_404(Category, slug=category_slug)
    threads = category.threads.select_related("author")
    return render(request, "forum/category_detail.html", {"category": category, "threads": threads})


def thread_detail(request, category_slug, thread_slug):
    thread = get_object_or_404(
        Thread.objects.select_related("category", "author"),
        category__slug=category_slug,
        slug=thread_slug,
    )
    posts = thread.posts.select_related("author")
    return render(request, "forum/thread_detail.html", {"thread": thread, "posts": posts})
