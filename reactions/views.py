from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .services import has_reacted, reaction_count, toggle_reaction

REACTABLE_MODELS = {
    "forum": {"post", "thread"},
    "games": {"review"},
}


def _get_target_or_404(app_label, model_name):
    if model_name not in REACTABLE_MODELS.get(app_label, set()):
        raise Http404("Not reactable.")
    content_type = get_object_or_404(ContentType, app_label=app_label, model=model_name)
    model_class = content_type.model_class()
    if model_class is None:
        raise Http404("Not reactable.")
    return model_class


@login_required
@require_POST
def toggle(request, app_label, model_name, pk):
    model_class = _get_target_or_404(app_label, model_name)
    obj = get_object_or_404(model_class, pk=pk)

    toggle_reaction(request.user, obj)

    if request.htmx:
        return render(
            request,
            "reactions/_like_button.html",
            {
                "target_app": app_label,
                "target_model": model_name,
                "target_pk": pk,
                "liked": has_reacted(request.user, obj),
                "count": reaction_count(obj),
            },
        )
    for candidate in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(candidate)
    return redirect("/")
