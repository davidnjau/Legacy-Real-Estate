import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0070_link_move_out_inspections_to_notices"),
    ]

    operations = [
        migrations.AddField(
            model_name="caretakerinvitation",
            name="properties",
            field=models.ManyToManyField(
                blank=True,
                related_name="multi_caretaker_invitations",
                to="users.property",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="active_caretaker_property",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="active_caretaker_profiles",
                to="users.property",
            ),
        ),
    ]
