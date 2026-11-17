from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0059_tenant_move_in_confirmation"),
    ]

    operations = [
        migrations.AddField(
            model_name="propertyunittype",
            name="unit_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
