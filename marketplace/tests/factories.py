from django.contrib.auth.models import User
from django.utils import timezone

from games.models import Game
from marketplace.models import ActivationPlatform, BuyerVerification, Product, Region
from marketplace.services import inventory as inventory_service

TEST_FERNET_KEY = "XfQIqLazZ-_9eFcOx_WO30tl98RJwFzk93RRVp3aUh0="


def make_user(username="buyer", verified=True, **kwargs):
    kwargs.setdefault("email", f"{username}@example.test")
    user = User.objects.create_user(username=username, password="testpass123", **kwargs)
    if verified and user.email:
        BuyerVerification.objects.create(user=user, email=user.email, verified_at=timezone.now())
    return user


def make_game(title="Test Game", **kwargs):
    slug = kwargs.pop("slug", title.lower().replace(" ", "-"))
    return Game.objects.create(title=title, slug=slug, **kwargs)


def make_product(game=None, unit_price_minor=1999, currency="USD", **kwargs):
    game = game or make_game()
    platform, _ = ActivationPlatform.objects.get_or_create(slug="steam", defaults={"name": "Steam"})
    region, _ = Region.objects.get_or_create(code="GLOBAL", defaults={"name": "Global"})
    slug = kwargs.pop("slug", f"{game.slug}-{platform.slug}-{region.code.lower()}")
    return Product.objects.create(
        game=game,
        activation_platform=platform,
        region=region,
        slug=slug,
        unit_price_minor=unit_price_minor,
        currency=currency,
        status=Product.Status.ACTIVE,
        is_purchasable=True,
        **kwargs,
    )


def make_keys(product, count, uploader, prefix="TEST"):
    plaintext_keys = [f"{prefix}-{product.pk}-{i:04d}" for i in range(count)]
    return inventory_service.import_keys(product, plaintext_keys, uploader)
