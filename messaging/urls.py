from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("messages/", views.inbox, name="inbox"),
    path("messages/users/", views.user_list, name="user-list"),
    path("messages/unread-count/", views.unread_count, name="unread-count"),
    path("messages/<str:username>/archive/", views.archive_conversation, name="archive"),
    path("messages/<str:username>/send/", views.send_message, name="send"),
    path("messages/<str:username>/", views.conversation, name="conversation"),
]