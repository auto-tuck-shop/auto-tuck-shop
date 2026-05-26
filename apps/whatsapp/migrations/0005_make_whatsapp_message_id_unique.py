# Generated migration to make whatsapp_message_id unique

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp', '0004_increase_phone_number_max_length'),
    ]

    operations = [
        migrations.AlterField(
            model_name='whatsappmessage',
            name='whatsapp_message_id',
            field=models.CharField(
                blank=True,
                help_text='Meta API message ID',
                max_length=100,
                unique=True,
            ),
        ),
    ]
