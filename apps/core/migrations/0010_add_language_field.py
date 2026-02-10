from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_add_account_manager_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='language',
            field=models.CharField(
                default='sn',
                help_text="User's preferred language (en, sn)",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='waitlistentry',
            name='language',
            field=models.CharField(
                default='sn',
                help_text="User's preferred language (en, sn)",
                max_length=10,
            ),
        ),
    ]
