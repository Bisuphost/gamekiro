from datetime import timedelta

from django.utils import timezone

RATE_LIMIT_WINDOW = timedelta(minutes=1)
RATE_LIMIT_MAX = {"thread": 3, "post": 10}


def is_rate_limited(user, model_class):
    limit = RATE_LIMIT_MAX.get(model_class._meta.model_name)
    if limit is None:
        return False
    window_start = timezone.now() - RATE_LIMIT_WINDOW
    return model_class.objects.filter(author=user, created_at__gte=window_start).count() >= limit
