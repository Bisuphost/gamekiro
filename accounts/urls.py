from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("edit/", views.profile_edit, name="profile-edit"),
    path("setup/", views.profile_setup, name="profile-setup"),
    path("u/games/add/", views.profile_game_add, name="profile-game-add"),
    path("u/games/<int:pk>/update/", views.profile_game_update, name="profile-game-update"),
    path("u/games/<int:pk>/remove/", views.profile_game_remove, name="profile-game-remove"),
    path("<str:username>/followers/", views.followers, name="followers"),
    path("<str:username>/following/", views.following, name="following"),
    path("<str:username>/", views.profile_detail, name="profile-detail"),
    path("accounts/signup/", views.signup, name="signup"),
    path("u/<str:username>/", views.profile, name="profile"),
]
