from django.db import migrations

ACTIVATION_PLATFORMS = [
    {"name": "Steam", "slug": "steam"},
    {"name": "Epic Games Store", "slug": "epic-games-store"},
    {"name": "GOG", "slug": "gog"},
    {"name": "Ubisoft Connect", "slug": "ubisoft-connect"},
    {"name": "Battle.net", "slug": "battle-net"},
]

REGIONS = [
    {"name": "Global", "code": "GLOBAL"},
    {"name": "Europe", "code": "EU"},
    {"name": "North America", "code": "NA"},
    {"name": "Asia", "code": "ASIA"},
]


def seed_reference_data(apps, schema_editor):
    ActivationPlatform = apps.get_model("marketplace", "ActivationPlatform")
    Region = apps.get_model("marketplace", "Region")
    for entry in ACTIVATION_PLATFORMS:
        ActivationPlatform.objects.get_or_create(slug=entry["slug"], defaults=entry)
    for entry in REGIONS:
        Region.objects.get_or_create(code=entry["code"], defaults=entry)


def remove_reference_data(apps, schema_editor):
    ActivationPlatform = apps.get_model("marketplace", "ActivationPlatform")
    Region = apps.get_model("marketplace", "Region")
    ActivationPlatform.objects.filter(
        slug__in=[entry["slug"] for entry in ACTIVATION_PLATFORMS]
    ).delete()
    Region.objects.filter(code__in=[entry["code"] for entry in REGIONS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0002_alter_gamekey_options_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_reference_data, remove_reference_data),
    ]
