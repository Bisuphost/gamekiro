from datetime import timedelta

from django.utils import timezone

from .models import Message

RATE_LIMIT_WINDOW = timedelta(minutes=1)
RATE_LIMIT_MAX = 20


def is_rate_limited(user):
	window_start = timezone.now() - RATE_LIMIT_WINDOW
	sent_count = Message.objects.filter(sender=user, created_at__gte=window_start).count()
	return sent_count >= RATE_LIMIT_MAX
