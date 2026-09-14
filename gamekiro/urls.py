from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("social/", include("social.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("core.urls")),
    path("", include("messaging.urls")),
    path("", include("forum.urls")),
    path("", include("reactions.urls")),
    path("", include("moderation.urls")),
    path("", include("notifications.urls")),
    path("", include("games.urls")),
    path("", include("news.urls")),
    path("", include("accounts.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
