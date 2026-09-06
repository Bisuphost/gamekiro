from django.db.models import Avg
from django.shortcuts import get_object_or_404, redirect, render

from reactions.services import has_reacted, reaction_counts_for

from .forms import ReviewForm
from .models import Game, Platform, Review


def game_list(request):
	games = Game.objects.prefetch_related("platforms", "tags")
	platforms = Platform.objects.all()
	selected_platform = request.GET.get("platform", "")
	selected_tag = request.GET.get("tag", "")

	if selected_platform:
		games = games.filter(platforms__slug=selected_platform)
	if selected_tag:
		games = games.filter(tags__slug=selected_tag)

	return render(
		request,
		"games/game_list.html",
		{
			"games": games.distinct(),
			"platforms": platforms,
			"selected_platform": selected_platform,
			"selected_tag": selected_tag,
		},
	)


def game_detail(request, slug):
	game = get_object_or_404(Game.objects.prefetch_related("platforms", "tags"), slug=slug)
	return _game_review_context(request, game)


def game_review(request, game_id):
	game = get_object_or_404(Game.objects.prefetch_related("platforms", "tags"), pk=game_id)
	return _game_review_context(request, game)


def _game_review_context(request, game):
	reviews = Review.objects.filter(game=game).select_related("user")
	review_list = list(reviews)
	review_counts = reaction_counts_for(review_list)
	for review in review_list:
		review.helpful_count = review_counts.get(review.pk, 0)
		review.user_has_helpful = has_reacted(request.user, review)

	average_rating = reviews.aggregate(avg=Avg("rating"))["avg"]
	if average_rating is None:
		average_rating = 0
	average_rating = round(average_rating, 1)

	user_review = None
	review_form = None
	if request.user.is_authenticated:
		user_review = reviews.filter(user=request.user).first()
		if request.method == "POST":
			review_form = ReviewForm(request.POST, instance=user_review)
			if review_form.is_valid():
				review = review_form.save(commit=False)
				review.game = game
				review.user = request.user
				review.save()
				return redirect("games:detail", slug=game.slug)
		else:
			review_form = ReviewForm(instance=user_review)

	if request.method == "POST" and not request.user.is_authenticated:
		return redirect("login")

	return render(
		request,
		"games/game_detail.html",
		{
			"game": game,
			"reviews": review_list,
			"average_rating": average_rating,
			"review_count": len(review_list),
			"review_form": review_form,
			"user_review": user_review,
		},
	)
