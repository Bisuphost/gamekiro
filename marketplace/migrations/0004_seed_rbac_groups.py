from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

GROUP_PERMISSIONS = {
    "Marketplace Support": [
        "view_order",
        "fulfill_order",
        "view_dashboard",
        "view_auditlog",
        "view_gamekey",
        "view_keyaccesslog",
        "view_entitlement",
    ],
    "Marketplace Manager": [
        "add_product",
        "change_product",
        "delete_product",
        "view_product",
        "add_productimage",
        "change_productimage",
        "delete_productimage",
        "view_productimage",
        "import_inventory",
        "add_coupon",
        "change_coupon",
        "delete_coupon",
        "view_coupon",
        "view_dashboard",
        "view_order",
        "fulfill_order",
    ],
    "Marketplace Finance": [
        "view_order",
        "refund_order",
        "view_payment",
        "view_refund",
        "add_refund",
        "change_refund",
        "view_dashboard",
        "view_auditlog",
    ],
}

ADMIN_GROUP = "Marketplace Admin"


def ensure_marketplace_permissions_exist():
    app_config = global_apps.get_app_config("marketplace")
    app_config.models_module = app_config.models_module or True
    create_permissions(app_config, verbosity=0)


def seed_groups(apps, schema_editor):
    ensure_marketplace_permissions_exist()
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    marketplace_permissions = Permission.objects.filter(content_type__app_label="marketplace")
    by_codename = {perm.codename: perm for perm in marketplace_permissions}

    for group_name, codenames in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        permissions = [by_codename[codename] for codename in codenames if codename in by_codename]
        group.permissions.set(permissions)

    admin_group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
    admin_group.permissions.set(marketplace_permissions)


def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(
        name__in=[*GROUP_PERMISSIONS.keys(), ADMIN_GROUP],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0003_seed_reference_data"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(seed_groups, remove_groups),
    ]
