from django.urls import path

from . import views

app_name = "moderation"

urlpatterns = [
    path(
        "report/<str:app_label>/<str:model_name>/<int:pk>/",
        views.report,
        name="report",
    ),
]
