from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect

from .models import Follow


@login_required
def toggle_follow(request, username):
    if request.method != "POST":
        return redirect("accounts:profile-detail", username=username)

    target = get_object_or_404(User, username=username)
    if target == request.user:
        return redirect("accounts:profile-detail", username=username)

    follow = Follow.objects.filter(follower=request.user, following=target)
    if follow.exists():
        follow.delete()
    else:
        Follow.objects.create(follower=request.user, following=target)

    if request.headers.get("HX-Request"):
        return HttpResponse(f"<span>{target.followers.count()} followers</span>")
    return redirect("accounts:profile-detail", username=username)
