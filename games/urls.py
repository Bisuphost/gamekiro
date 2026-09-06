from django.urls import path

from . import views

app_name = "games"

urlpatterns = [
    path("games/", views.game_list, name="list"),
    path("games/<int:game_id>/review/", views.game_review, name="review"),
    path("games/<slug:slug>/", views.game_detail, name="detail"),
]