from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from marketplace.models import Product
from marketplace.services import inventory as inventory_service


class Command(BaseCommand):
    help = "Import game keys for a product from a text file, one key per line."

    def add_arguments(self, parser):
        parser.add_argument("product_slug")
        parser.add_argument("keys_file")
        parser.add_argument(
            "--username", required=True, help="Username attributed as the importer."
        )

    def handle(self, *args, **options):
        try:
            product = Product.objects.get(slug=options["product_slug"])
        except Product.DoesNotExist as exc:
            raise CommandError(f"No product with slug {options['product_slug']!r}") from exc

        User = get_user_model()
        try:
            uploader = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user named {options['username']!r}") from exc

        with open(options["keys_file"], encoding="utf-8") as handle:
            plaintext_keys = [line.strip() for line in handle if line.strip()]

        if not plaintext_keys:
            raise CommandError("No keys found in file.")

        batch = inventory_service.import_keys(product, plaintext_keys, uploader, source="cli")
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {batch.imported_count} keys "
                f"({batch.duplicate_count} duplicates, {batch.invalid_count} invalid)."
            )
        )
