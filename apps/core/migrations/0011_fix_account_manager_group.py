from django.db import migrations


def fix_account_manager_group(apps, schema_editor):
    """Re-create Account Manager group using get_or_create on ContentType.

    0009 used ContentType.objects.get() which fails in manage.py test because
    the ContentType table is empty during migration (post_migrate hasn't fired).
    get_or_create creates the row inline if absent, bypassing that signal.
    """
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    # Remove whatever 0009 may have created (idempotent on fresh DBs)
    Group.objects.filter(name="Account Manager").delete()

    ct, _ = ContentType.objects.get_or_create(
        app_label="core",
        model="waitlistentry",
    )
    group, _ = Group.objects.get_or_create(name="Account Manager")
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
        ("core", "0010_add_language_field"),
    ]

    operations = [
        migrations.RunPython(
            fix_account_manager_group,
            remove_account_manager_group,
        ),
    ]
