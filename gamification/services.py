from forum.models import Post, Thread
from reputation.models import KarmaScore

from .models import Badge, UserBadge


def _check_karma_50(user_id):
    score = KarmaScore.objects.filter(user_id=user_id).values_list("score", flat=True).first() or 0
    return score >= 50


def _check_posts_10(user_id):
    return Post.objects.filter(author_id=user_id).count() >= 10


def _check_first_thread(user_id):
    return Thread.objects.filter(author_id=user_id).exists()


CRITERIA_CHECKS = {
    Badge.CriteriaKey.KARMA_50: _check_karma_50,
    Badge.CriteriaKey.POSTS_10: _check_posts_10,
    Badge.CriteriaKey.FIRST_THREAD: _check_first_thread,
}


def check_badges(user_id):
    already_earned = set(
        UserBadge.objects.filter(user_id=user_id).values_list("badge_id", flat=True)
    )
    awarded = []
    for badge in Badge.objects.filter(is_active=True):
        if badge.id in already_earned:
            continue
        check = CRITERIA_CHECKS.get(badge.criteria_key)
        if check and check(user_id):
            _, created = UserBadge.objects.get_or_create(user_id=user_id, badge_id=badge.id)
            if created:
                awarded.append(badge)
    return awarded
