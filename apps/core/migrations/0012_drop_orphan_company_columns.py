from django.db import migrations


class Migration(migrations.Migration):
    """
    Drop columns that exist in the DB but not in the model.
    These were added manually during development and never cleaned up.
    """

    dependencies = [
        ('core', '0011_fix_account_manager_group'),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE core_company DROP COLUMN IF EXISTS daily_summary_enabled;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
