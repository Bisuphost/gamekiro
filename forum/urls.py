from django.urls import path

from . import views

app_name = "forum"

urlpatterns = [
    path("forum/", views.category_list, name="category_list"),
    path("forum/new/", views.thread_create_any, name="thread_create_any"),
    path("forum/search/", views.search, name="search"),
    path("forum/upload-image/", views.upload_post_image, name="upload_post_image"),
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
