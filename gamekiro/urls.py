from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.views import robots_txt
from gamekiro.sitemaps import sitemaps

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("robots.txt", robots_txt, name="robots_txt"),
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": sitemaps},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("social/", include("social.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("store/", include("marketplace.urls")),
    path("", include("core.urls")),
    path("", include("messaging.urls")),
    path("", include("forum.urls")),
    path("", include("reactions.urls")),
    path("", include("moderation.urls")),
    path("", include("notifications.urls")),
    path("", include("games.urls")),
    path("", include("news.urls")),
    path("", include("tournaments.urls")),
    path("", include("accounts.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
