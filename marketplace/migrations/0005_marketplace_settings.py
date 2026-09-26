from django.db import migrations


def create_settings(apps, schema_editor):
    MarketplaceSettings = apps.get_model("marketplace", "MarketplaceSettings")
    MarketplaceSettings.objects.get_or_create(
        pk=1,
        defaults={
            "marketplace_enabled": False,
            "purchases_enabled": True,
            "fulfillment_enabled": True,
        },
    )


def remove_settings(apps, schema_editor):
    MarketplaceSettings = apps.get_model("marketplace", "MarketplaceSettings")
    MarketplaceSettings.objects.filter(pk=1).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0004_seed_rbac_groups"),
    ]

    operations = [
        migrations.RunPython(create_settings, remove_settings),
    ]
