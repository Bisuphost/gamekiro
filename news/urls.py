from django.urls import path

from . import views

app_name = "news"

urlpatterns = [
    path("news/", views.article_list, name="list"),
    path("news/<slug:slug>/", views.article_detail, name="detail"),
]
