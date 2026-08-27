from django.contrib.contenttypes.models import ContentType

from forum.models import Post, Thread
from gamification.models import Badge
from messaging.models import Message

from .models import Notification

TARGET_LOADERS = {
    "post": lambda ids: Post.objects.select_related("thread", "thread__category").filter(
        pk__in=ids
    ),
    "thread": lambda ids: Thread.objects.select_related("category").filter(pk__in=ids),
    "message": lambda ids: Message.objects.filter(pk__in=ids),
    "badge": lambda ids: Badge.objects.filter(pk__in=ids),
}


def notify(recipient, actor, verb, target=None):
    if recipient == actor:
        return None
    return Notification.objects.create(
        recipient=recipient,
        actor=actor,
        verb=verb,
        content_type=ContentType.objects.get_for_model(target) if target is not None else None,
        object_id=target.pk if target is not None else None,
    )


def attach_targets(notifications):
    by_content_type = {}
    for notification in notifications:
        if notification.content_type_id is not None:
            by_content_type.setdefault(notification.content_type_id, []).append(notification)

    for group in by_content_type.values():
        content_type = group[0].content_type
        loader = TARGET_LOADERS.get(content_type.model)
        if loader is None:
            continue
        ids = [notification.object_id for notification in group]
        objects_by_pk = {obj.pk: obj for obj in loader(ids)}
        for notification in group:
            obj = objects_by_pk.get(notification.object_id)
            if obj is not None:
                notification.target = obj
