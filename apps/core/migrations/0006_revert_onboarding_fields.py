# Revert migration 0005_add_onboarding_fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_add_onboarding_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='userprofile',
            name='last_summary_date',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='daily_summary_enabled',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='onboarding_step',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='language',
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                choices=[
                    ('owner', 'Owner'),
                    ('assistant', 'Assistant'),
                ],
                default='assistant',
                max_length=20
            ),
        ),
    ]
