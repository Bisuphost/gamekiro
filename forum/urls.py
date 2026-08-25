from django.urls import path

from . import views

app_name = "forum"

urlpatterns = [
    path("forum/", views.category_list, name="category_list"),
    path("forum/<slug:category_slug>/", views.category_detail, name="category_detail"),
    path("forum/<slug:category_slug>/new/", views.thread_create, name="thread_create"),
    path(
        "forum/<slug:category_slug>/<slug:thread_slug>/",
        views.thread_detail,
        name="thread_detail",
    ),
    path(
        "forum/<slug:category_slug>/<slug:thread_slug>/reply/",
        views.post_create,
        name="post_create",
    ),
]
