from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from marketplace.models import CronLease, FulfillmentJob, MarketplaceSettings, Order, OrderItem
from marketplace.services import jobs as jobs_service

from . import factories


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class CronLeaseTests(TestCase):
    def test_second_acquire_fails_while_first_holds_the_lease(self):
        first = jobs_service.acquire_lease("test-lease", ttl_seconds=60)
        self.assertIsNotNone(first)

        second = jobs_service.acquire_lease("test-lease", ttl_seconds=60)
        self.assertIsNone(second)

    def test_release_then_reacquire_succeeds(self):
        holder = jobs_service.acquire_lease("test-lease", ttl_seconds=60)
        jobs_service.release_lease("test-lease", holder)

        second = jobs_service.acquire_lease("test-lease", ttl_seconds=60)
        self.assertIsNotNone(second)

    def test_expired_lease_can_be_reclaimed(self):
        holder = jobs_service.acquire_lease("test-lease", ttl_seconds=60)
        CronLease.objects.filter(name="test-lease").update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
        second = jobs_service.acquire_lease("test-lease", ttl_seconds=60)
        self.assertIsNotNone(second)
        self.assertNotEqual(holder, second)

    def test_heartbeat_creates_and_updates_row(self):
        jobs_service.heartbeat()
        first = CronLease.objects.get(name="heartbeat")
        first_time = first.expires_at

        jobs_service.heartbeat()
        second = CronLease.objects.get(name="heartbeat")
        self.assertGreaterEqual(second.expires_at, first_time)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class DrainFulfillmentJobsTests(TestCase):
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
        self.staff = factories.make_user("staff")
        self.buyer = factories.make_user("buyer")
        self.product = factories.make_product(unit_price_minor=500)
        factories.make_keys(self.product, 1, self.staff)

    def test_drain_picks_up_pending_job_and_fulfils_it(self):
        order = Order.objects.create(
            user=self.buyer, currency="USD", total_minor=500, status=Order.Status.PAID
        )
        item = OrderItem.objects.create(
            order=order, product=self.product, unit_price_minor=500, product_title="x"
        )
        from marketplace.services import inventory as inventory_service

        inventory_service.reserve_key_for_item(item)
        FulfillmentJob.objects.create(order=order)

        result = jobs_service.drain_fulfillment_jobs(budget=10)
        self.assertEqual(result["attempted"], 1)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)


@override_settings(MARKETPLACE_ENABLED=True, MARKETPLACE_KEY_ENC_KEY=factories.TEST_FERNET_KEY)
class RunMarketplaceJobsCommandTests(TestCase):
    def test_command_runs_cleanly_with_empty_queues(self):
        out = StringIO()
        call_command("run_marketplace_jobs", stdout=out)
        self.assertIn("completed", out.getvalue())
        self.assertTrue(CronLease.objects.filter(name="heartbeat").exists())
