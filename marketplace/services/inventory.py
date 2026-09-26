import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from marketplace.models import GameKey, InventoryImportBatch, Order

from . import keys as keys_service

logger = logging.getLogger("marketplace")

CANDIDATE_FANOUT = 25
EXPIRY_CHUNK_SIZE = 500


class OutOfStock(Exception):
    pass


def available_count(product):
    return GameKey.objects.filter(
        product=product, status=GameKey.Status.AVAILABLE, reserved_by_item__isnull=True
    ).count()


def reserve_key_for_item(order_item, ttl_minutes=None, now=None):
    now = now or timezone.now()
    ttl_minutes = ttl_minutes or settings.MARKETPLACE_RESERVATION_TTL_MINUTES
    expires = now + timedelta(minutes=ttl_minutes)
    claim = uuid.uuid4()

    candidate_ids = list(
        GameKey.objects.filter(
            product_id=order_item.product_id,
            status=GameKey.Status.AVAILABLE,
            reserved_by_item__isnull=True,
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:CANDIDATE_FANOUT]
    )

    for key_id in candidate_ids:
        claimed = GameKey.objects.filter(
            pk=key_id,
            status=GameKey.Status.AVAILABLE,
            reserved_by_item__isnull=True,
        ).update(
            status=GameKey.Status.RESERVED,
            reserved_by_item=order_item,
            claim_token=claim,
            reserved_at=now,
            reservation_expires_at=expires,
            updated_at=now,
        )
        if claimed:
            return GameKey.objects.get(pk=key_id)

    if candidate_ids and available_count(order_item.product_id) > 0:
        logger.warning(
            "Candidate fanout exhausted while stock remains for product %s — "
            "consider raising CANDIDATE_FANOUT.",
            order_item.product_id,
        )
    raise OutOfStock(f"No available keys for product {order_item.product_id}")


def reserve_keys_for_order(order, ttl_minutes=None, now=None):
    now = now or timezone.now()
    reserved = []
    for item in order.items.select_related("product").all():
        reserved.append(reserve_key_for_item(item, ttl_minutes=ttl_minutes, now=now))
    return reserved


def release_reservation_for_order(order, now=None):
    now = now or timezone.now()
    item_ids = list(order.items.values_list("pk", flat=True))
    if not item_ids:
        return 0
    return GameKey.objects.filter(
        reserved_by_item_id__in=item_ids, status=GameKey.Status.RESERVED
    ).update(
        status=GameKey.Status.AVAILABLE,
        reserved_by_item=None,
        claim_token=None,
        reserved_at=None,
        reservation_expires_at=None,
        updated_at=now,
    )


def assign_key_for_item(order_item, now=None):
    now = now or timezone.now()
    key = getattr(order_item, "reserved_key", None)
    if key is None:
        return False
    updated = GameKey.objects.filter(
        pk=key.pk, status=GameKey.Status.RESERVED, reserved_by_item=order_item
    ).update(
        status=GameKey.Status.ASSIGNED,
        sold_at=now,
        reservation_expires_at=None,
        updated_at=now,
    )
    if not updated:
        return False
    type(order_item).objects.filter(pk=order_item.pk, delivered_at__isnull=True).update(
        delivered_at=now, updated_at=now
    )
    return True


def mark_key_delivered(key, now=None):
    now = now or timezone.now()
    updated = GameKey.objects.filter(pk=key.pk, status=GameKey.Status.ASSIGNED).update(
        status=GameKey.Status.DELIVERED, updated_at=now
    )
    return bool(updated)


def _expire_orders_and_release_keys(order_queryset, now, limit):
    order_ids = list(
        order_queryset.filter(
            status=Order.Status.PENDING_PAYMENT, reservation_expires_at__lt=now
        ).values_list("pk", flat=True)[:limit]
    )
    if not order_ids:
        return 0, 0

    expired_count = Order.objects.filter(
        pk__in=order_ids, status=Order.Status.PENDING_PAYMENT
    ).update(status=Order.Status.EXPIRED, updated_at=now)

    key_ids = list(
        GameKey.objects.filter(
            reserved_by_item__order_id__in=order_ids,
            reserved_by_item__order__status=Order.Status.EXPIRED,
            status=GameKey.Status.RESERVED,
        ).values_list("pk", flat=True)
    )
    released_count = 0
    if key_ids:
        released_count = GameKey.objects.filter(
            pk__in=key_ids, status=GameKey.Status.RESERVED
        ).update(
            status=GameKey.Status.AVAILABLE,
            reserved_by_item=None,
            claim_token=None,
            reserved_at=None,
            reservation_expires_at=None,
            updated_at=now,
        )
    return expired_count, released_count


def sweep_expired_reservations(now=None, limit=EXPIRY_CHUNK_SIZE):
    now = now or timezone.now()
    return _expire_orders_and_release_keys(Order.objects.all(), now, limit)


def sweep_expired_reservations_for_product(product, now=None, limit=100):
    now = now or timezone.now()
    order_qs = Order.objects.filter(items__product=product).distinct()
    return _expire_orders_and_release_keys(order_qs, now, limit)


def import_keys(product, plaintext_keys, uploaded_by, source="manual", notes=""):
    cleaned_keys = [raw.strip() for raw in plaintext_keys]
    batch = InventoryImportBatch.objects.create(
        product=product,
        uploaded_by=uploaded_by,
        source=source,
        total_count=len(cleaned_keys),
        notes=notes,
    )

    imported = duplicates = invalid = 0
    for raw in cleaned_keys:
        if not raw:
            invalid += 1
            continue
        fingerprint = keys_service.fingerprint(raw)
        if GameKey.objects.filter(product=product, key_fingerprint=fingerprint).exists():
            duplicates += 1
            continue
        try:
            with transaction.atomic():
                GameKey.objects.create(
                    product=product,
                    encrypted_key=keys_service.encrypt(raw),
                    key_fingerprint=fingerprint,
                    masked_hint=keys_service.mask(raw),
                    import_batch=batch,
                    imported_by=uploaded_by,
                )
            imported += 1
        except IntegrityError:
            duplicates += 1

    batch.imported_count = imported
    batch.duplicate_count = duplicates
    batch.invalid_count = invalid
    batch.save(update_fields=["imported_count", "duplicate_count", "invalid_count", "updated_at"])
    return batch
