from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0049_viewinglead_propertyviewing_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="property",
            name="expected_unit_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="property",
            name="default_unit_type",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
