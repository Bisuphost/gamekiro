from celery import shared_task
from django.contrib.auth import get_user_model

from gamification.services import check_badges

from .services import recalculate_karma


@shared_task
def recalculate_karma_task(user_id):
    recalculate_karma(user_id)
    check_badges(user_id)


@shared_task
def recalculate_all_karma_task():
    User = get_user_model()
    for user_id in User.objects.values_list("id", flat=True):
        recalculate_karma(user_id)
        check_badges(user_id)
