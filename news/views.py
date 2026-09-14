from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Article

ARTICLES_PER_PAGE = 12


def article_list(request):
    articles = Article.objects.filter(
        is_published=True, published_at__lte=timezone.now()
    ).select_related("author")
    paginator = Paginator(articles, ARTICLES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "news/list.html", {"articles": page})


def article_detail(request, slug):
    article = get_object_or_404(Article.objects.select_related("author"), slug=slug)
    can_preview = request.user.is_authenticated and request.user.is_staff
    if not article.is_visible and not can_preview:
        raise Http404
    return render(request, "news/detail.html", {"article": article})
