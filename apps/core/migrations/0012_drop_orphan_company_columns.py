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
            sql="""
                ALTER TABLE core_company
                    DROP COLUMN IF EXISTS daily_summary_enabled,
                    DROP COLUMN IF EXISTS daily_closing_date,
                    DROP COLUMN IF EXISTS daily_closing_time,
                    DROP COLUMN IF EXISTS last_closing_prompt_date,
                    DROP COLUMN IF EXISTS last_summary_date,
                    DROP COLUMN IF EXISTS normal_closing_time;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
