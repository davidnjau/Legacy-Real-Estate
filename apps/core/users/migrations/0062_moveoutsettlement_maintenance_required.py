from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0061_unit_month_invoice_waivers"),
    ]

    operations = [
        migrations.AddField(
            model_name="propertyinspection",
            name="actual_move_out_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="propertyinspection",
            name="final_readings",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="propertyinspection",
            name="damage_items",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="propertyinspection",
            name="maintenance_required",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="moveoutsettlement",
            name="maintenance_required",
            field=models.BooleanField(default=False),
        ),
    ]
