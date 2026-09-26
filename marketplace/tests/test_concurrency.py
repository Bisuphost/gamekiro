import threading

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings, tag

from marketplace.models import GameKey, MarketplaceSettings, Order, OrderItem
from marketplace.services import inventory as inventory_service

from . import factories


@tag("concurrency")
@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class ConcurrentAllocationTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        MarketplaceSettings.objects.update_or_create(
            pk=1,
            defaults={
                "marketplace_enabled": True,
                "purchases_enabled": True,
                "fulfillment_enabled": True,
            },
        )

    def test_twenty_threads_five_keys_exactly_five_succeed(self):
        staff = factories.make_user("staff")
        product = factories.make_product(unit_price_minor=999)
        factories.make_keys(product, 5, staff)

        buyer = factories.make_user("buyer")
        order = Order.objects.create(user=buyer, currency="USD", total_minor=999 * 20)
        items = [
            OrderItem.objects.create(
                order=order,
                product=product,
                unit_price_minor=999,
                product_title=str(product),
            )
            for _ in range(20)
        ]

        results = {}
        errors = {}

        def worker(item):
            try:
                key = inventory_service.reserve_key_for_item(item)
                results[item.pk] = key.pk
            except inventory_service.OutOfStock:
                errors[item.pk] = "out_of_stock"
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(item,)) for item in items]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 5)
        self.assertEqual(len(errors), 15)
        self.assertEqual(len(set(results.values())), 5)

        sold_keys = GameKey.objects.filter(product=product, status=GameKey.Status.RESERVED)
        self.assertEqual(sold_keys.count(), 5)
