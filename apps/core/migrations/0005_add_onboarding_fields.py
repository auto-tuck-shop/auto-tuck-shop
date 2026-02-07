# Generated migration for onboarding fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_add_waitlist_confirmation_sid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                choices=[
                    ('owner', 'Owner'),
                    ('assistant', 'Assistant'),
                    ('both', 'Both (Owner & Assistant)')
                ],
                default='assistant',
                max_length=20
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='language',
            field=models.CharField(
                default='en',
                help_text="User's preferred language (en, sn)",
                max_length=10
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='onboarding_step',
            field=models.CharField(
                choices=[
                    ('complete', 'Complete'),
                    ('language', 'Language Selection'),
                    ('role', 'Role Selection'),
                    ('assistant_link', 'Assistant Linking'),
                    ('stock_setup', 'Stock Setup'),
                    ('stock_adding', 'Adding Stock Items')
                ],
                default='language',
                help_text='Current onboarding step',
                max_length=20
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='daily_summary_enabled',
            field=models.BooleanField(default=True, help_text='Send daily summary'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='last_summary_date',
            field=models.DateField(blank=True, null=True, help_text='Last date a summary was sent/requested'),
        ),
    ]
