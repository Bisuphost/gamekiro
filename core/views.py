from django.contrib.auth import get_user_model
from django.db.models import Avg, Count
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.generic import TemplateView

from forum.models import Category, Post, Thread
from games.models import Game, Review
from gamification.models import UserBadge
from moderation.services import hidden_object_ids
from news.models import Article
from reputation.models import KarmaScore

KARMA_BADGE_THRESHOLD = 50


def home(request):
    if request.user.is_authenticated and request.session.get("onboarding_required"):
        return redirect("accounts:profile-setup")

    published_articles = Article.objects.filter(
        is_published=True, published_at__lte=timezone.now()
    ).select_related("author")
    featured_article = published_articles.first()
    latest_news = published_articles[1:4] if featured_article else published_articles[:3]

    recent_threads = Thread.objects.select_related("category", "author").order_by("-created_at")[:4]

    popular_categories = Category.objects.annotate(thread_count=Count("threads")).order_by(
        "-thread_count", "order", "name"
    )[:6]

    top_contributors = (
        KarmaScore.objects.filter(score__gt=0)
        .select_related("user", "user__profile")
        .order_by("-score")[:4]
    )

    recent_reviews = (
        Review.objects.exclude(pk__in=hidden_object_ids(Review))
        .select_related("user", "user__profile", "game")
        .order_by("-created_at")[:4]
    )

    recent_badges = UserBadge.objects.select_related("user", "user__profile", "badge").order_by(
        "-awarded_at"
    )[:4]

    top_rated_games = list(
        Game.objects.annotate(avg_rating=Avg("reviews__rating"), review_count=Count("reviews"))
        .filter(review_count__gt=0)
        .order_by("-avg_rating", "-review_count")[:6]
    )
    for game in top_rated_games:
        game.rounded_rating = round(game.avg_rating)

    User = get_user_model()
    site_stats = {
        "member_count": User.objects.count(),
        "game_count": Game.objects.count(),
        "thread_count": Thread.objects.count(),
        "post_count": Post.objects.count(),
    }

    greeting_hour = timezone.localtime().hour
    if greeting_hour < 12:
        greeting = "Good morning"
    elif greeting_hour < 18:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    personal_stats = None
    if request.user.is_authenticated:
        karma_score = (
            KarmaScore.objects.filter(user=request.user).values_list("score", flat=True).first()
            or 0
        )
        personal_stats = {
            "karma_score": karma_score,
            "karma_progress_percent": min(
                100, round(max(karma_score, 0) / KARMA_BADGE_THRESHOLD * 100)
            ),
            "karma_badge_threshold": KARMA_BADGE_THRESHOLD,
            "badge_count": UserBadge.objects.filter(user=request.user).count(),
            "thread_count": Thread.objects.filter(author=request.user).count(),
            "post_count": Post.objects.filter(author=request.user).count(),
        }

    return render(
        request,
        "core/home.html",
        {
            "featured_article": featured_article,
            "latest_news": latest_news,
            "recent_threads": recent_threads,
            "popular_categories": popular_categories,
            "top_contributors": top_contributors,
            "recent_reviews": recent_reviews,
            "recent_badges": recent_badges,
            "top_rated_games": top_rated_games,
            "site_stats": site_stats,
            "personal_stats": personal_stats,
            "greeting": greeting,
        },
    )


class PrivacyPolicyView(TemplateView):
    template_name = "core/privacy_policy.html"


class TermsOfServiceView(TemplateView):
    template_name = "core/terms_of_service.html"


class CookiePolicyView(TemplateView):
    template_name = "core/cookie_policy.html"


class RefundPolicyView(TemplateView):
    template_name = "core/refund_policy.html"
