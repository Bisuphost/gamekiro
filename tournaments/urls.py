from django.urls import path

from . import views

app_name = "tournaments"

urlpatterns = [
    path("tournaments/", views.tournament_list, name="tournament_list"),
    path("tournaments/apply/", views.organizer_apply, name="organizer_apply"),
    path("tournaments/dashboard/", views.organizer_dashboard, name="organizer_dashboard"),
    path("tournaments/new/", views.tournament_create, name="tournament_create"),
    path("tournaments/<slug:slug>/", views.tournament_detail, name="tournament_detail"),
    path("tournaments/<slug:slug>/edit/", views.tournament_edit, name="tournament_edit"),
    path("tournaments/<slug:slug>/cancel/", views.tournament_cancel, name="tournament_cancel"),
    path("tournaments/<slug:slug>/join/", views.tournament_join, name="tournament_join"),
    path("tournaments/<slug:slug>/leave/", views.tournament_leave, name="tournament_leave"),
]
