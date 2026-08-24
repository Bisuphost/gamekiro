from django.urls import path

from . import views

app_name = "social"

urlpatterns = [
    path("<str:username>/toggle-follow/", views.toggle_follow, name="toggle-follow"),
]
