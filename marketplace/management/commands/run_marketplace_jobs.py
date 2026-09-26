from django.core.management.base import BaseCommand

from marketplace.services import jobs as jobs_service

LEASE_NAME = "run_marketplace_jobs"


class Command(BaseCommand):
    help = (
        "Drain the marketplace's cron-driven jobs: reservation expiry, fulfilment, "
        "webhook reconciliation, outbound email."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--budget", type=int, default=50, help="Max items to process per job type."
        )

    def handle(self, *args, **options):
        budget = options["budget"]

        holder = jobs_service.acquire_lease(LEASE_NAME)
        if holder is None:
            self.stdout.write("Another run_marketplace_jobs is already in progress. Skipping.")
            return

        try:
            expiry = jobs_service.expire_reservations()
            self.stdout.write(f"Expired reservations: {expiry}")

            fulfilment = jobs_service.drain_fulfillment_jobs(budget=budget)
            self.stdout.write(f"Fulfilment jobs: {fulfilment}")

            webhooks = jobs_service.reconcile_stuck_webhooks(budget=budget)
            self.stdout.write(f"Webhook reconciliation: {webhooks}")

            pulled = jobs_service.pull_reconciliation(budget=budget)
            self.stdout.write(f"Pull reconciliation: {pulled}")

            refunds = jobs_service.reconcile_refunds(budget=budget)
            self.stdout.write(f"Refund reconciliation: {refunds}")

            emails = jobs_service.drain_outbound_email(budget=budget)
            self.stdout.write(f"Outbound email: {emails}")

            purged = jobs_service.purge_old_webhook_payloads()
            self.stdout.write(f"Purged webhook payloads: {purged}")

            jobs_service.purge_stale_rate_limits()
            jobs_service.heartbeat()
            self.stdout.write(self.style.SUCCESS("run_marketplace_jobs completed."))
        finally:
            jobs_service.release_lease(LEASE_NAME, holder)
