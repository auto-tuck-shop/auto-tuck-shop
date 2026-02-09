from django.db import migrations


def create_account_manager_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct = ContentType.objects.get(app_label="core", model="waitlistentry")
    group = Group.objects.create(name="Account Manager")
    perms = Permission.objects.filter(
        content_type=ct,
        codename__in=["view_waitlistentry", "change_waitlistentry"],
    )
    group.permissions.set(perms)


def remove_account_manager_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="Account Manager").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_increase_phone_number_max_length"),
    ]

    operations = [
        migrations.RunPython(
            create_account_manager_group,
            remove_account_manager_group,
        ),
    ]
