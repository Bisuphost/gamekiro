from django import template

from reputation.models import KarmaScore

register = template.Library()


@register.inclusion_tag("reputation/_karma.html")
def karma_badge(user):
    score = KarmaScore.objects.filter(user=user).values_list("score", flat=True).first() or 0
    return {"score": score}
