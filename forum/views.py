from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import PostForm, ThreadForm
from .models import Category, Post, Thread

THREADS_PER_PAGE = 20


def category_list(request):
    categories = Category.objects.all()
    return render(request, "forum/category_list.html", {"categories": categories})


def category_detail(request, category_slug):
    category = get_object_or_404(Category, slug=category_slug)
    thread_qs = category.threads.select_related("author")
    paginator = Paginator(thread_qs, THREADS_PER_PAGE)
    threads = paginator.get_page(request.GET.get("page"))
    return render(request, "forum/category_detail.html", {"category": category, "threads": threads})


def thread_detail(request, category_slug, thread_slug):
    thread = get_object_or_404(
        Thread.objects.select_related("category", "author"),
        category__slug=category_slug,
        slug=thread_slug,
    )
    posts = thread.posts.select_related("author")
    reply_form = PostForm() if request.user.is_authenticated else None
    return render(
        request,
        "forum/thread_detail.html",
        {"thread": thread, "posts": posts, "reply_form": reply_form},
    )


def _unique_thread_slug(category, title):
    base = slugify(title)[:255] or "thread"
    slug = base
    suffix = 2
    while Thread.objects.filter(category=category, slug=slug).exists():
        tail = f"-{suffix}"
        slug = f"{base[: 255 - len(tail)]}{tail}"
        suffix += 1
    return slug


@login_required
def thread_create(request, category_slug):
    category = get_object_or_404(Category, slug=category_slug)
    if request.method == "POST":
        form = ThreadForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                thread = Thread.objects.create(
                    category=category,
                    author=request.user,
                    title=form.cleaned_data["title"],
                    slug=_unique_thread_slug(category, form.cleaned_data["title"]),
                )
                Post.objects.create(
                    thread=thread, author=request.user, body=form.cleaned_data["body"]
                )
            return redirect(
                "forum:thread_detail", category_slug=category.slug, thread_slug=thread.slug
            )
    else:
        form = ThreadForm()
    return render(request, "forum/thread_form.html", {"category": category, "form": form})


@login_required
@require_POST
def post_create(request, category_slug, thread_slug):
    thread = get_object_or_404(Thread, category__slug=category_slug, slug=thread_slug)
    if thread.is_locked:
        messages.error(request, "This thread is locked.")
        return redirect("forum:thread_detail", category_slug=category_slug, thread_slug=thread_slug)

    form = PostForm(request.POST)
    if form.is_valid():
        Post.objects.create(thread=thread, author=request.user, body=form.cleaned_data["body"])
    else:
        messages.error(request, "Couldn't post your reply — please check the form.")
    return redirect("forum:thread_detail", category_slug=category_slug, thread_slug=thread_slug)
