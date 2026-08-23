from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("accounts/signup/", views.signup, name="signup"),
    path("u/<str:username>/", views.profile, name="profile"),
]
