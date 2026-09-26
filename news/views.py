from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Article

ARTICLES_PER_PAGE = 12
FEATURED_COUNT = 2
SECONDARY_COUNT = 3


def article_list(request):
    articles = Article.objects.filter(
        is_published=True, published_at__lte=timezone.now()
    ).select_related("author")

    query = request.GET.get("q", "").strip()
    if query:
        articles = articles.filter(title__icontains=query)

    hero_count = FEATURED_COUNT + SECONDARY_COUNT
    remaining = articles[hero_count:]
    paginator = Paginator(remaining, ARTICLES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    context = {"articles": page, "query": query}
    if page.number == 1:
        hero_articles = list(articles[:hero_count])
        context["featured_articles"] = hero_articles[:FEATURED_COUNT]
        context["secondary_articles"] = hero_articles[FEATURED_COUNT:hero_count]

    return render(request, "news/list.html", context)


def article_detail(request, slug):
    article = get_object_or_404(Article.objects.select_related("author"), slug=slug)
    can_preview = request.user.is_authenticated and request.user.is_staff
    if not article.is_visible and not can_preview:
        raise Http404
    return render(request, "news/detail.html", {"article": article})
