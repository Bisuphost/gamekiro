from django.urls import path

from . import views

app_name = "reactions"

urlpatterns = [
    path(
        "react/<str:app_label>/<str:model_name>/<int:pk>/toggle/",
        views.toggle,
        name="toggle",
    ),
]
