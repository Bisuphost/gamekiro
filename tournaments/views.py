from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import OrganizerApplicationForm, TournamentForm
from .models import OrganizerProfile, Tournament, TournamentParticipant

PAST_PER_PAGE = 12


def tournament_list(request):
    now = timezone.now()
    base_qs = Tournament.objects.filter(is_cancelled=False).select_related("organizer__user")

    live = base_qs.filter(starts_at__lte=now, ends_at__gte=now)
    joinable = [t for t in base_qs.filter(starts_at__gt=now) if not t.is_full]

    past_qs = (
        Tournament.objects.filter(ends_at__lt=now)
        .select_related("organizer__user")
        .order_by("-ends_at")
    )
    paginator = Paginator(past_qs, PAST_PER_PAGE)
    past = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "tournaments/list.html",
        {"live": live, "joinable": joinable, "past": past},
    )


def tournament_detail(request, slug):
    tournament = get_object_or_404(Tournament.objects.select_related("organizer__user"), slug=slug)
    can_view_room = tournament.can_view_room_details(request.user)
    is_participant = tournament.is_participant(request.user)
    is_organizer = request.user.is_authenticated and request.user == tournament.organizer.user
    return render(
        request,
        "tournaments/detail.html",
        {
            "tournament": tournament,
            "can_view_room": can_view_room,
            "is_participant": is_participant,
            "is_organizer": is_organizer,
        },
    )


@login_required
def organizer_apply(request):
    existing = OrganizerProfile.objects.filter(user=request.user).first()
    if existing:
        if existing.is_approved:
            return redirect("tournaments:organizer_dashboard")
        return render(request, "tournaments/organizer_apply.html", {"existing": existing})

    if request.method == "POST":
        form = OrganizerApplicationForm(request.POST)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            messages.success(request, "Application submitted — we'll review it soon.")
            return redirect("tournaments:organizer_apply")
    else:
        form = OrganizerApplicationForm()
    return render(request, "tournaments/organizer_apply.html", {"form": form})


@login_required
def organizer_dashboard(request):
    profile = OrganizerProfile.objects.filter(user=request.user, status="approved").first()
    if not profile:
        messages.error(request, "You need an approved organizer application to see this page.")
        return redirect("tournaments:organizer_apply")
    tournaments = profile.tournaments.all()
    return render(
        request,
        "tournaments/organizer_dashboard.html",
        {"profile": profile, "tournaments": tournaments},
    )


def _unique_tournament_slug(title):
    base = slugify(title)[:220] or "tournament"
    slug = base
    suffix = 2
    while Tournament.objects.filter(slug=slug).exists():
        tail = f"-{suffix}"
        slug = f"{base[: 220 - len(tail)]}{tail}"
        suffix += 1
    return slug


@login_required
def tournament_create(request):
    profile = OrganizerProfile.objects.filter(user=request.user, status="approved").first()
    if not profile:
        messages.error(request, "You need an approved organizer application to host a tournament.")
        return redirect("tournaments:organizer_apply")

    if request.method == "POST":
        form = TournamentForm(request.POST)
        if form.is_valid():
            tournament = form.save(commit=False)
            tournament.organizer = profile
            tournament.slug = _unique_tournament_slug(form.cleaned_data["title"])
            tournament.save()
            messages.success(request, "Tournament listed.")
            return redirect("tournaments:tournament_detail", slug=tournament.slug)
    else:
        form = TournamentForm()
    return render(request, "tournaments/tournament_form.html", {"form": form, "is_new": True})


@login_required
def tournament_edit(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug)
    if request.user != tournament.organizer.user:
        messages.error(request, "You can only edit your own tournaments.")
        return redirect("tournaments:tournament_detail", slug=slug)
    if tournament.is_past:
        messages.error(request, "This tournament has already ended.")
        return redirect("tournaments:tournament_detail", slug=slug)

    if request.method == "POST":
        form = TournamentForm(request.POST, instance=tournament)
        if form.is_valid():
            form.save()
            messages.success(request, "Tournament updated.")
            return redirect("tournaments:tournament_detail", slug=tournament.slug)
    else:
        form = TournamentForm(instance=tournament)
    return render(
        request,
        "tournaments/tournament_form.html",
        {"form": form, "is_new": False, "tournament": tournament},
    )


@login_required
@require_POST
def tournament_cancel(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug)
    if request.user != tournament.organizer.user:
        messages.error(request, "You can only cancel your own tournaments.")
        return redirect("tournaments:tournament_detail", slug=slug)
    tournament.is_cancelled = True
    tournament.save(update_fields=["is_cancelled"])
    messages.success(request, "Tournament cancelled.")
    return redirect("tournaments:tournament_detail", slug=slug)


@login_required
@require_POST
def tournament_join(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug)
    if not tournament.is_joinable:
        messages.error(request, "This tournament isn't open for joining.")
    elif tournament.is_participant(request.user):
        messages.info(request, "You're already in this tournament.")
    else:
        try:
            TournamentParticipant.objects.create(tournament=tournament, user=request.user)
        except IntegrityError:
            messages.info(request, "You're already in this tournament.")
        else:
            messages.success(request, "You're in! Room details are below.")
    return redirect("tournaments:tournament_detail", slug=slug)


@login_required
@require_POST
def tournament_leave(request, slug):
    tournament = get_object_or_404(Tournament, slug=slug)
    if tournament.is_past:
        messages.error(request, "This tournament has already ended.")
    else:
        TournamentParticipant.objects.filter(tournament=tournament, user=request.user).delete()
        messages.success(request, "You've left the tournament.")
    return redirect("tournaments:tournament_detail", slug=slug)
