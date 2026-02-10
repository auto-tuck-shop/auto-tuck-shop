from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0007_alter_sale_company"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="flagged_as_bot_mistake",
            field=models.BooleanField(default=False, help_text="Flagged by user as a bot misinterpretation"),
        ),
    ]
