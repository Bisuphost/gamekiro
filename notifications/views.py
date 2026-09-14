from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Notification
from .services import attach_targets

NOTIFICATIONS_PER_PAGE = 20
RECENT_NOTIFICATIONS_LIMIT = 8


@login_required
def notification_list(request):
    notification_qs = Notification.objects.filter(recipient=request.user).select_related(
        "actor", "content_type"
    )
    if request.htmx:
        recent = list(notification_qs[:RECENT_NOTIFICATIONS_LIMIT])
        attach_targets(recent)
        return render(
            request, "notifications/_notification_list_body.html", {"notifications": recent}
        )

    paginator = Paginator(notification_qs, NOTIFICATIONS_PER_PAGE)
    notifications = paginator.get_page(request.GET.get("page"))
    attach_targets(notifications.object_list)
    return render(request, "notifications/list.html", {"notifications": notifications})


@login_required
@require_POST
def mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.is_read = True
    notification.save(update_fields=["is_read"])

    if request.htmx:
        attach_targets([notification])
        return render(
            request,
            "notifications/_notification_row.html",
            {"notification": notification, "page_number": ""},
        )

    page = request.POST.get("page", "")
    list_url = reverse("notifications:list")
    if page.isdigit():
        list_url = f"{list_url}?page={page}"
    return redirect(list_url)


@login_required
def unread_count(request):
    count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return render(request, "notifications/_unread_count.html", {"unread_count": count})
