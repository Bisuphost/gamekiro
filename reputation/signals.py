from kombu.exceptions import OperationalError

from .models import KarmaEvent
from .tasks import recalculate_karma_task

POINTS_BY_MODEL = {"thread": 3, "post": 2}


def handle_reaction_saved(sender, instance, created, **kwargs):
    if created:
        _record_and_dispatch(instance, sign=1, reason=KarmaEvent.Reason.LIKE_RECEIVED)


def handle_reaction_deleted(sender, instance, **kwargs):
    _record_and_dispatch(instance, sign=-1, reason=KarmaEvent.Reason.LIKE_REMOVED)


def _record_and_dispatch(reaction, sign, reason):
    target = reaction.target
    if target is None or not hasattr(target, "author"):
        return

    points = POINTS_BY_MODEL.get(reaction.content_type.model)
    if points is None:
        return

    KarmaEvent.objects.create(
        user=target.author,
        points=points * sign,
        reason=reason,
        content_type=reaction.content_type,
        object_id=reaction.object_id,
    )

    try:
        recalculate_karma_task.delay(target.author_id)
    except OperationalError:
        pass
