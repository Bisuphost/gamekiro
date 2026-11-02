from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("u/", include("accounts.urls")),
    path("social/", include("social.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("forum.urls")),
    path("", include("tournaments.urls")),
    path("", include("core.urls")),
    path("", include("accounts.urls")),
]
