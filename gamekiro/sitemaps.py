from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from forum.models import Category, Thread
from games.models import Game
from marketplace.models import Product
from moderation.services import hidden_object_ids
from news.models import Article
from tournaments.models import Tournament


class StaticViewSitemap(Sitemap):
    priority = 0.6
    changefreq = "weekly"

    def items(self):
        return [
            "core:home",
            "games:list",
            "news:list",
            "forum:category_list",
            "marketplace:home",
            "marketplace:browse",
            "tournaments:tournament_list",
        ]

    def location(self, item):
        return reverse(item)


class GameSitemap(Sitemap):
    priority = 0.7
    changefreq = "weekly"

    def items(self):
        return Game.objects.order_by("title")

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse("games:detail", args=[obj.slug])


class ArticleSitemap(Sitemap):
    priority = 0.6
    changefreq = "monthly"

    def items(self):
        return Article.objects.filter(
            is_published=True, published_at__lte=timezone.now()
        ).order_by("-published_at")

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse("news:detail", args=[obj.slug])


class ThreadSitemap(Sitemap):
    priority = 0.4
    changefreq = "weekly"

    def items(self):
        return (
            Thread.objects.exclude(pk__in=hidden_object_ids(Thread))
            .select_related("category")
            .order_by("-created_at")
        )

    def lastmod(self, obj):
        return obj.created_at

    def location(self, obj):
        return reverse("forum:thread_detail", args=[obj.category.slug, obj.slug])


class ForumCategorySitemap(Sitemap):
    priority = 0.5
    changefreq = "weekly"

    def items(self):
        return Category.objects.order_by("order", "name")

    def location(self, obj):
        return reverse("forum:category_detail", args=[obj.slug])


class TournamentSitemap(Sitemap):
    priority = 0.5
    changefreq = "daily"

    def items(self):
        return Tournament.objects.filter(is_cancelled=False).order_by("-starts_at")

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse("tournaments:tournament_detail", args=[obj.slug])


class ProductSitemap(Sitemap):
    priority = 0.8
    changefreq = "daily"

    def items(self):
        return Product.objects.filter(status=Product.Status.ACTIVE).order_by("slug")

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse("marketplace:product_detail", args=[obj.slug])


sitemaps = {
    "static": StaticViewSitemap,
    "games": GameSitemap,
    "news": ArticleSitemap,
    "forum": ThreadSitemap,
    "forum-categories": ForumCategorySitemap,
    "tournaments": TournamentSitemap,
    "store": ProductSitemap,
}
