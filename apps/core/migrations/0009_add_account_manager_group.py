from django.db import migrations


class Migration(migrations.Migration):
    """Originally created Account Manager group via RunPython, but that
    implementation used ContentType.objects.get() which fails on fresh
    test databases (ContentType table is empty before post_migrate fires).
    Logic moved to 0011_fix_account_manager_group which uses get_or_create."""

    dependencies = [
        ("core", "0008_increase_phone_number_max_length"),
    ]

    operations = [
        migrations.RunSQL("SELECT 1", migrations.RunSQL.noop),
    ]
