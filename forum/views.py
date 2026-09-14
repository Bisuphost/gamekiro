from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from moderation.models import Report
from moderation.services import hidden_object_ids
from notifications.models import Notification
from notifications.services import notify
from reactions.services import has_reacted, liked_pks_for, reaction_count, reaction_counts_for

from .forms import PostForm, PostImageUploadForm, ThreadCreateAnyForm, ThreadForm
from .models import POSTGRES, Category, Post, PostImage, Thread
from .services import is_rate_limited

if POSTGRES:
    from django.contrib.postgres.search import SearchQuery, SearchRank


def _search_filter(queryset, query):
    """Rank by PostgreSQL full-text search where available, else icontains."""
    if POSTGRES:
        search_query = SearchQuery(query, config="english")
        return (
            queryset.filter(title_search_vector=search_query)
            .annotate(rank=SearchRank(F("title_search_vector"), search_query))
            .order_by("-rank")
        )
    return queryset.filter(title__icontains=query)

THREADS_PER_PAGE = 20
POSTS_PER_PAGE = 20


def category_list(request):
    categories = Category.objects.all()
    return render(request, "forum/category_list.html", {"categories": categories})


def category_detail(request, category_slug):
    category = get_object_or_404(Category, slug=category_slug)
    thread_qs = category.threads.select_related("author").exclude(pk__in=hidden_object_ids(Thread))

    query = request.GET.get("q", "").strip()
    if query:
        thread_qs = _search_filter(thread_qs, query)

    status_filter = request.GET.get("filter", "")
    if status_filter not in ("pinned", "locked"):
        status_filter = ""
    if status_filter == "pinned":
        thread_qs = thread_qs.filter(is_pinned=True)
    elif status_filter == "locked":
        thread_qs = thread_qs.filter(is_locked=True)

    paginator = Paginator(thread_qs, THREADS_PER_PAGE)
    threads = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "forum/category_detail.html",
        {
            "category": category,
            "threads": threads,
            "query": query,
            "status_filter": status_filter,
        },
    )


def search(request):
    query = request.GET.get("q", "").strip()
    threads = Thread.objects.none()
    if query:
        threads = _search_filter(
            Thread.objects.select_related("category", "author").exclude(
                pk__in=hidden_object_ids(Thread)
            ),
            query,
        )
    paginator = Paginator(threads, THREADS_PER_PAGE)
    results = paginator.get_page(request.GET.get("page"))
    return render(request, "forum/search.html", {"query": query, "threads": results})


def thread_detail(request, category_slug, thread_slug):
    thread = get_object_or_404(
        Thread.objects.select_related("category", "author"),
        category__slug=category_slug,
        slug=thread_slug,
    )
    post_qs = thread.posts.select_related("author").exclude(pk__in=hidden_object_ids(Post))
    paginator = Paginator(post_qs, POSTS_PER_PAGE)
    posts = paginator.get_page(request.GET.get("page"))

    post_list = list(posts.object_list)
    counts = reaction_counts_for(post_list)
    liked_pks = liked_pks_for(request.user, post_list)
    for post in post_list:
        post.like_count = counts.get(post.pk, 0)
        post.user_has_liked = post.pk in liked_pks

    thread.like_count = reaction_count(thread)
    thread.user_has_liked = has_reacted(request.user, thread)

    reply_form = PostForm() if request.user.is_authenticated else None
    return render(
        request,
        "forum/thread_detail.html",
        {
            "thread": thread,
            "posts": posts,
            "reply_form": reply_form,
            "report_reasons": Report.Reason.choices,
        },
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
        if is_rate_limited(request.user, Thread):
            messages.error(
                request,
                "You're posting too fast — please wait a moment before creating another thread.",
            )
            return redirect("forum:category_detail", category_slug=category_slug)
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
def thread_create_any(request):
    if request.method == "POST":
        if is_rate_limited(request.user, Thread):
            messages.error(
                request,
                "You're posting too fast — please wait a moment before creating another thread.",
            )
            return redirect("forum:category_list")
        form = ThreadCreateAnyForm(request.POST)
        if form.is_valid():
            category = form.cleaned_data["category"]
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
        form = ThreadCreateAnyForm()
    return render(request, "forum/thread_form_any.html", {"form": form})


@login_required
@require_POST
def post_create(request, category_slug, thread_slug):
    thread = get_object_or_404(
        Thread.objects.select_related("author"), category__slug=category_slug, slug=thread_slug
    )
    if thread.is_locked:
        messages.error(request, "This thread is locked.")
        return redirect("forum:thread_detail", category_slug=category_slug, thread_slug=thread_slug)

    if is_rate_limited(request.user, Post):
        messages.error(
            request, "You're posting too fast — please wait a moment before replying again."
        )
        return redirect("forum:thread_detail", category_slug=category_slug, thread_slug=thread_slug)

    form = PostForm(request.POST)
    if form.is_valid():
        post = Post.objects.create(
            thread=thread, author=request.user, body=form.cleaned_data["body"]
        )
        if thread.author_id != request.user.id:
            notify(
                recipient=thread.author,
                actor=request.user,
                verb=Notification.Verb.THREAD_REPLY,
                target=post,
            )
    else:
        messages.error(request, "Couldn't post your reply — please check the form.")
    return redirect("forum:thread_detail", category_slug=category_slug, thread_slug=thread_slug)


@login_required
@require_POST
def upload_post_image(request):
    if is_rate_limited(request.user, PostImage, field_name="uploader"):
        return JsonResponse(
            {"error": "You're uploading too fast — please wait a moment."}, status=429
        )

    form = PostImageUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        error = next(iter(form.errors.get("image", ["Invalid image."])), "Invalid image.")
        return JsonResponse({"error": error}, status=400)

    post_image = form.save(commit=False)
    post_image.uploader = request.user
    post_image.save()
    return JsonResponse({"url": post_image.image.url})
