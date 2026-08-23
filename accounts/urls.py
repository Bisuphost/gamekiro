from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("edit/", views.profile_edit, name="profile-edit"),
    path("<str:username>/followers/", views.followers, name="followers"),
    path("<str:username>/following/", views.following, name="following"),
    path("<str:username>/", views.profile_detail, name="profile-detail"),
]
