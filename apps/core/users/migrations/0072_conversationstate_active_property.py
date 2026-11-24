import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0071_caretaker_multiple_properties_and_active_property"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversationstate",
            name="active_property",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="active_whatsapp_conversations",
                to="users.property",
            ),
        ),
    ]
