from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp', '0005_alter_whatsappmessage_button_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='whatsappmessage',
            name='whatsapp_message_id',
            field=models.CharField(
                blank=True,
                null=True,
                help_text='Meta API message ID',
                max_length=100,
            ),
        ),
        migrations.AddConstraint(
            model_name='whatsappmessage',
            constraint=models.UniqueConstraint(
                fields=['whatsapp_message_id'],
                condition=models.Q(whatsapp_message_id__isnull=False) & ~models.Q(whatsapp_message_id=''),
                name='unique_whatsapp_message_id_when_set',
            ),
        ),
    ]
