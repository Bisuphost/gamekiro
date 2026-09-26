from django.db import transaction
from django.utils import timezone

from marketplace.models import RateLimitCounter


def hit(action, identifier, limit, window_seconds=60, now=None):
    now = now or timezone.now()
    bucket = int(now.timestamp() // window_seconds)
    key = f"{action}:{identifier}:{bucket}"

    with transaction.atomic():
        counter, created = RateLimitCounter.objects.select_for_update().get_or_create(
            key=key, defaults={"window_start": now, "count": 1}
        )
        if not created:
            # select_for_update() above holds the row lock for this whole atomic
            # block, so this read-modify-write can't race with another hit() on
            # the same key — unlike an UPDATE ... SET count = count + 1 followed
            # by a separate re-SELECT, which can read a value bumped by a
            # different, already-committed concurrent caller and let several
            # callers believe they got the same count (an attacker's route past
            # a low limit under a concurrent burst).
            counter.count += 1
            counter.updated_at = now
            counter.save(update_fields=["count", "updated_at"])

    return counter.count <= limit


def peek(action, identifier, window_seconds=60, now=None):
    now = now or timezone.now()
    bucket = int(now.timestamp() // window_seconds)
    key = f"{action}:{identifier}:{bucket}"
    counter = RateLimitCounter.objects.filter(key=key).only("count").first()
    return counter.count if counter else 0


def purge_stale(older_than_seconds=3600, now=None):
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(seconds=older_than_seconds)
    return RateLimitCounter.objects.filter(window_start__lt=cutoff).delete()[0]
