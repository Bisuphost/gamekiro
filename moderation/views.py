from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import ReportForm
from .models import Report
from .services import REPORTABLE_MODELS


def _get_target_or_404(app_label, model_name):
    if model_name not in REPORTABLE_MODELS.get(app_label, set()):
        raise Http404("Not reportable.")
    content_type = get_object_or_404(ContentType, app_label=app_label, model=model_name)
    model_class = content_type.model_class()
    if model_class is None:
        raise Http404("Not reportable.")
    return model_class


@login_required
@require_POST
def report(request, app_label, model_name, pk):
    model_class = _get_target_or_404(app_label, model_name)
    obj = get_object_or_404(model_class, pk=pk)
    form = ReportForm(request.POST)
    if form.is_valid():
        content_type = ContentType.objects.get_for_model(model_class)
        _, created = Report.objects.get_or_create(
            reporter=request.user,
            content_type=content_type,
            object_id=obj.pk,
            defaults={
                "reason": form.cleaned_data["reason"],
                "detail": form.cleaned_data["detail"],
            },
        )
        if created:
            messages.success(request, "Thanks — this has been reported to the moderators.")
        else:
            messages.info(request, "You've already reported this.")
    else:
        messages.error(request, "Couldn't submit your report — please check the form.")

    for candidate in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(candidate)
    return redirect("/")
