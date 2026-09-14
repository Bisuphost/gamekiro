from django.db import migrations


def add_website_column(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == "postgresql":
            cursor.execute(
                "ALTER TABLE accounts_profile "
                "ADD COLUMN IF NOT EXISTS website varchar(200) NOT NULL DEFAULT '';"
            )
        else:
            cursor.execute("PRAGMA table_info(accounts_profile)")
            columns = [row[1] for row in cursor.fetchall()]
            if "website" not in columns:
                cursor.execute(
                    "ALTER TABLE accounts_profile "
                    "ADD COLUMN website varchar(200) NOT NULL DEFAULT '';"
                )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_add_profile_timestamps"),
    ]

    operations = [
        migrations.RunPython(add_website_column, migrations.RunPython.noop),
    ]
