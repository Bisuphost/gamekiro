from django.shortcuts import get_object_or_404, render

from .models import Game, Platform


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
	return render(request, "games/game_detail.html", {"game": game})
