from django.contrib.contenttypes.models import ContentType

from forum.models import Post, Thread
from reactions.models import Reaction

from .models import KarmaScore

POINTS_PER_POST_LIKE = 2
POINTS_PER_THREAD_LIKE = 3
POINTS_PER_THREAD_CREATED = 1
POINTS_PER_REVIEW_WRITTEN = 1


def recalculate_karma(user_id):
    thread_ct = ContentType.objects.get_for_model(Thread)
    post_ct = ContentType.objects.get_for_model(Post)

    threads_created = Thread.objects.filter(author_id=user_id).count()
    likes_on_threads = Reaction.objects.filter(
        content_type=thread_ct,
        object_id__in=Thread.objects.filter(author_id=user_id).values("id"),
    ).count()
    likes_on_posts = Reaction.objects.filter(
        content_type=post_ct,
        object_id__in=Post.objects.filter(author_id=user_id).values("id"),
    ).count()
    reviews_written = 0

    score = (
        likes_on_posts * POINTS_PER_POST_LIKE
        + likes_on_threads * POINTS_PER_THREAD_LIKE
        + threads_created * POINTS_PER_THREAD_CREATED
        + reviews_written * POINTS_PER_REVIEW_WRITTEN
    )

    KarmaScore.objects.update_or_create(user_id=user_id, defaults={"score": score})
    return score
