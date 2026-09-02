from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Profile
from forum.models import Category, Post, Thread
from games.models import Game, Platform


class Command(BaseCommand):
    help = "Create realistic demo data for staging and local demos."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Remove demo data before seeding.")

    def handle(self, *args, **options):
        self.stdout.write("Seeding GameKiro demo data...\n")
        with transaction.atomic():
            if options["clear"]:
                User = get_user_model()
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

            platform, _ = Platform.objects.get_or_create(slug="demo-console", defaults={"name": "Demo Console"})
            game, _ = Game.objects.get_or_create(
                slug="demo-rift",
                defaults={
                    "title": "Demo Rift",
                    "description": "A sample co-op adventure for local demos.",
                },
            )
            if not game.platforms.filter(pk=platform.pk).exists():
                game.platforms.add(platform)

            category, _ = Category.objects.get_or_create(
                slug="demo-community",
                defaults={"name": "Demo Community", "description": "A sample community hub for demos."},
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
            self.stdout.write(self.style.SUCCESS("✓ Threads created"))
            self.stdout.write(self.style.SUCCESS("✓ Posts created"))
            self.stdout.write(self.style.SUCCESS("\nDemo data successfully loaded."))
