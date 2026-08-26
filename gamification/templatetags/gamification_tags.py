from django import template

from gamification.models import UserBadge

register = template.Library()


@register.inclusion_tag("gamification/_achievements.html")
def achievement_badges(user):
    user_badges = (
        UserBadge.objects.filter(user=user).select_related("badge").order_by("-awarded_at")
    )
    return {"user_badges": user_badges}
