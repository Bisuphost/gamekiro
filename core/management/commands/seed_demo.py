from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Profile
from forum.models import Category, Post, Thread
from games.models import Game, Platform
from marketplace.models import ActivationPlatform, GameKey, InventoryImportBatch, Product, Region
from marketplace.services import inventory as inventory_service


class Command(BaseCommand):
    help = "Create realistic demo data for staging and local demos."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Remove demo data before seeding.")

    def handle(self, *args, **options):
        self.stdout.write("Seeding GameKiro demo data...\n")
        with transaction.atomic():
            if options["clear"]:
                User = get_user_model()
                GameKey.objects.filter(product__game__slug__startswith="demo-").delete()
                InventoryImportBatch.objects.filter(
                    product__game__slug__startswith="demo-"
                ).delete()
                Product.objects.filter(game__slug__startswith="demo-").delete()
                User.objects.filter(username__startswith="demo_").delete()
                Platform.objects.filter(slug__startswith="demo-").delete()
                Game.objects.filter(slug__startswith="demo-").delete()
                Category.objects.filter(slug__startswith="demo-").delete()

            User = get_user_model()
            users = []
            for index in range(1, 4):
                username = f"demo_player_{index}"
                email = f"{username}@example.com"
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={"email": email, "first_name": f"Demo {index}", "last_name": "Player"},
                )
                if created:
                    user.set_password("demo12345")
                    user.save()
                Profile.objects.get_or_create(user=user)
                users.append(user)

            platform, _ = Platform.objects.get_or_create(
                slug="demo-console", defaults={"name": "Demo Console"}
            )
            game, _ = Game.objects.get_or_create(
                slug="demo-rift",
                defaults={
                    "title": "Demo Rift",
                    "description": "A sample co-op adventure for local demos.",
                },
            )
            if not game.platforms.filter(pk=platform.pk).exists():
                game.platforms.add(platform)

            game_two, _ = Game.objects.get_or_create(
                slug="demo-skybound",
                defaults={
                    "title": "Demo Skybound",
                    "description": "A sample open-world flight game for local demos.",
                },
            )
            if not game_two.platforms.filter(pk=platform.pk).exists():
                game_two.platforms.add(platform)

            steam, _ = ActivationPlatform.objects.get_or_create(
                slug="steam", defaults={"name": "Steam"}
            )
            region, _ = Region.objects.get_or_create(code="GLOBAL", defaults={"name": "Global"})

            demo_staff, staff_created = User.objects.get_or_create(
                username="demo_marketplace_staff",
                defaults={"email": "demo_marketplace_staff@example.com"},
            )
            if staff_created:
                demo_staff.set_password("demo12345")
                demo_staff.save()

            demo_products = [
                (
                    game,
                    "demo-rift-steam-global",
                    1999,
                    2499,
                    "A sample co-op adventure for local demos.",
                ),
                (
                    game_two,
                    "demo-skybound-steam-global",
                    2999,
                    None,
                    "A sample open-world flight game for local demos.",
                ),
            ]
            for demo_game, slug, price, compare_price, description in demo_products:
                product, _ = Product.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "game": demo_game,
                        "activation_platform": steam,
                        "region": region,
                        "unit_price_minor": price,
                        "compare_at_price_minor": compare_price,
                        "currency": "USD",
                        "status": Product.Status.ACTIVE,
                        "is_purchasable": True,
                        "description": description,
                    },
                )
                if (
                    django_settings.MARKETPLACE_KEY_ENC_KEY
                    and not GameKey.objects.filter(product=product).exists()
                ):
                    inventory_service.import_keys(
                        product,
                        [f"DEMO-{slug.upper()}-{i:04d}" for i in range(1, 11)],
                        demo_staff,
                        source="demo",
                    )

            category, _ = Category.objects.get_or_create(
                slug="demo-community",
                defaults={
                    "name": "Demo Community",
                    "description": "A sample community hub for demos.",
                },
            )

            for index in range(1, 4):
                title = f"Demo thread {index}"
                thread, thread_created = Thread.objects.get_or_create(
                    category=category,
                    slug=f"demo-thread-{index}",
                    defaults={"title": title, "author": users[(index - 1) % len(users)]},
                )
                if thread_created:
                    Post.objects.get_or_create(
                        thread=thread,
                        author=thread.author,
                        body=f"This is a sample discussion post for {thread.title}.",
                    )

            self.stdout.write(self.style.SUCCESS("✓ Users created"))
            self.stdout.write(self.style.SUCCESS("✓ Profiles created"))
            self.stdout.write(self.style.SUCCESS("✓ Games created"))
            self.stdout.write(self.style.SUCCESS("✓ Store products created"))
            if django_settings.MARKETPLACE_KEY_ENC_KEY:
                self.stdout.write(self.style.SUCCESS("✓ Store demo keys imported"))
            else:
                self.stdout.write(
                    "  (skipped demo keys: MARKETPLACE_KEY_ENC_KEY is not configured)"
                )
            self.stdout.write(self.style.SUCCESS("✓ Threads created"))
            self.stdout.write(self.style.SUCCESS("✓ Posts created"))
            self.stdout.write(self.style.SUCCESS("\nDemo data successfully loaded."))
