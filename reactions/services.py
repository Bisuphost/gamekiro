from django.contrib.contenttypes.models import ContentType
from django.db.models import Count

from .models import Reaction


def toggle_reaction(user, obj):
    content_type = ContentType.objects.get_for_model(obj)
    reaction, created = Reaction.objects.get_or_create(
        user=user, content_type=content_type, object_id=obj.pk
    )
    if not created:
        reaction.delete()
    return created, reaction_count(obj)


def reaction_count(obj):
    content_type = ContentType.objects.get_for_model(obj)
    return Reaction.objects.filter(content_type=content_type, object_id=obj.pk).count()


def has_reacted(user, obj):
    if not user.is_authenticated:
        return False
    content_type = ContentType.objects.get_for_model(obj)
    return Reaction.objects.filter(user=user, content_type=content_type, object_id=obj.pk).exists()


def reaction_counts_for(objs):
    objs = list(objs)
    if not objs:
        return {}
    content_type = ContentType.objects.get_for_model(objs[0])
    pks = [o.pk for o in objs]
    rows = (
        Reaction.objects.filter(content_type=content_type, object_id__in=pks)
        .values("object_id")
        .annotate(n=Count("id"))
    )
    return {row["object_id"]: row["n"] for row in rows}


def liked_pks_for(user, objs):
    objs = list(objs)
    if not user.is_authenticated or not objs:
        return set()
    content_type = ContentType.objects.get_for_model(objs[0])
    pks = [o.pk for o in objs]
    return set(
        Reaction.objects.filter(
            user=user, content_type=content_type, object_id__in=pks
        ).values_list("object_id", flat=True)
    )
