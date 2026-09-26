from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from marketplace.services import ratelimit as ratelimit_service
from marketplace.services import webhooks as webhooks_service

from .utils import client_ip

WEBHOOK_IP_RATE_LIMIT = 120
WEBHOOK_IP_RATE_WINDOW_SECONDS = 60


@csrf_exempt
@require_POST
def webhook_receiver(request, provider):
    max_bytes = settings.MARKETPLACE_WEBHOOK_MAX_BODY_BYTES
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                return HttpResponse("Payload too large.", status=413)
        except ValueError:
            return HttpResponse("Invalid Content-Length.", status=400)

    if not ratelimit_service.hit(
        "webhook_ip", client_ip(request), WEBHOOK_IP_RATE_LIMIT, WEBHOOK_IP_RATE_WINDOW_SECONDS
    ):
        return HttpResponse("Too many requests.", status=429)

    raw_body = request.body
    if len(raw_body) > max_bytes:
        return HttpResponse("Payload too large.", status=413)

    try:
        status_code, message = webhooks_service.handle(provider, raw_body, request.headers)
    except ValueError:
        return HttpResponseNotFound("Unknown payment provider.")
    return HttpResponse(message, status=status_code)
