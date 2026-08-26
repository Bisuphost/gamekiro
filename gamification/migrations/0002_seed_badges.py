from django.db import migrations

BADGES = [
    {"name": "Karma Star", "criteria_key": "karma_50", "icon": "🌟"},
    {"name": "Prolific Poster", "criteria_key": "posts_10", "icon": "📝"},
    {"name": "First Thread", "criteria_key": "first_thread", "icon": "🎉"},
]


def seed_badges(apps, schema_editor):
    Badge = apps.get_model("gamification", "Badge")
    for entry in BADGES:
        Badge.objects.get_or_create(
            criteria_key=entry["criteria_key"],
            defaults={
                "name": entry["name"],
                "icon": entry["icon"],
                "is_active": True,
            },
        )


def remove_badges(apps, schema_editor):
    Badge = apps.get_model("gamification", "Badge")
    Badge.objects.filter(criteria_key__in=[entry["criteria_key"] for entry in BADGES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("gamification", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_badges, remove_badges),
    ]
