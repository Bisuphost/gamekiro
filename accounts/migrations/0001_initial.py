from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Profile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bio", models.TextField(blank=True)),
                ("avatar", models.ImageField(blank=True, null=True, upload_to="avatars/")),
                ("discord_username", models.CharField(blank=True, max_length=100)),
                ("steam_id", models.CharField(blank=True, max_length=100)),
                ("psn_id", models.CharField(blank=True, max_length=100)),
                ("other_links", models.URLField(blank=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
