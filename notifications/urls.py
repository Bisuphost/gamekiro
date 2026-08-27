from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("notifications/", views.notification_list, name="list"),
    path("notifications/unread-count/", views.unread_count, name="unread-count"),
    path("notifications/<int:pk>/read/", views.mark_read, name="mark-read"),
]
