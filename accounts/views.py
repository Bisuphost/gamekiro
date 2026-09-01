from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProfileForm, ProfileGameForm, SignupForm
from .models import ProfileGame


def profile_detail(request, username):
    profile_user = get_object_or_404(User, username=username)
    return render(
        request,
        "accounts/profile_detail.html",
        {
            "profile_user": profile_user,
            "profile_games": profile_user.profile.profile_games.select_related("game"),
            "profile_game_form": ProfileGameForm() if request.user == profile_user else None,
        },
    )


@login_required
def profile_edit(request):
    form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("accounts:profile-detail", username=request.user.username)
    return render(request, "accounts/profile_edit.html", {"form": form})


def followers(request, username):
    profile_user = get_object_or_404(User, username=username)
    users = User.objects.filter(following__following=profile_user).select_related("profile")
    return render(
        request,
        "accounts/user_list.html",
        {"profile_user": profile_user, "users": users, "list_type": "Followers"},
    )


def following(request, username):
    profile_user = get_object_or_404(User, username=username)
    users = User.objects.filter(followers__follower=profile_user).select_related("profile")
    return render(
        request,
        "accounts/user_list.html",
        {"profile_user": profile_user, "users": users, "list_type": "Following"},
    )


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("core:home")
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", {"form": form})


def profile(request, username):
    profile_user = get_object_or_404(User, username=username)
    profile_games = profile_user.profile.profile_games.select_related("game")
    return render(
        request,
        "accounts/profile.html",
        {
            "profile_user": profile_user,
            "profile_games": profile_games,
            "profile_game_form": ProfileGameForm() if request.user == profile_user else None,
        },
    )


@login_required
def profile_game_add(request):
    if request.method != "POST":
        return redirect("accounts:profile", username=request.user.username)
    form = ProfileGameForm(request.POST)
    if form.is_valid():
        ProfileGame.objects.update_or_create(
            profile=request.user.profile,
            game=form.cleaned_data["game"],
            defaults={"status": form.cleaned_data["status"]},
        )
        return redirect("accounts:profile", username=request.user.username)
    profile_games = request.user.profile.profile_games.select_related("game")
    return render(
        request,
        "accounts/profile.html",
        {
            "profile_user": request.user,
            "profile_games": profile_games,
            "profile_game_form": form,
        },
        status=400,
    )


@login_required
def profile_game_update(request, pk):
    profile_game = get_object_or_404(ProfileGame, pk=pk, profile=request.user.profile)
    if request.method == "POST":
        form = ProfileGameForm(request.POST, instance=profile_game)
        if form.is_valid():
            form.save()
    return redirect("accounts:profile", username=request.user.username)


@login_required
def profile_game_remove(request, pk):
    profile_game = get_object_or_404(ProfileGame, pk=pk, profile=request.user.profile)
    if request.method == "POST":
        profile_game.delete()
    return redirect("accounts:profile", username=request.user.username)
