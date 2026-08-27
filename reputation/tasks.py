from celery import shared_task
from django.contrib.auth import get_user_model

from gamification.services import check_badges
from notifications.models import Notification
from notifications.services import notify

from .services import recalculate_karma


def _recalculate_and_notify(user_id):
    recalculate_karma(user_id)
    awarded = check_badges(user_id)
    if awarded:
        recipient = get_user_model().objects.get(pk=user_id)
        for badge in awarded:
            notify(
                recipient=recipient, actor=None, verb=Notification.Verb.BADGE_EARNED, target=badge
            )


@shared_task
def recalculate_karma_task(user_id):
    _recalculate_and_notify(user_id)


@shared_task
def recalculate_all_karma_task():
    User = get_user_model()
    for user_id in User.objects.values_list("id", flat=True):
        _recalculate_and_notify(user_id)
