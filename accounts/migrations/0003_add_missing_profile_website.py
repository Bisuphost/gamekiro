from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_add_profile_timestamps"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE accounts_profile "
                "ADD COLUMN IF NOT EXISTS website varchar(200) NOT NULL DEFAULT '';"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
