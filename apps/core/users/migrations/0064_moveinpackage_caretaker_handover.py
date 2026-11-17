import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def mark_existing_packages_legacy(apps, schema_editor):
    MoveInPackage = apps.get_model("users", "MoveInPackage")
    MoveInPackage.objects.all().update(handover_status="legacy")


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0063_meterreadingdraft_photo_key_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="moveinpackage",
            name="condition_checklist",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="handover_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="handover_confirmed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="confirmed_move_in_handovers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="handover_photos",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="handover_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending caretaker handover"),
                    ("submitted", "Awaiting Property Officer confirmation"),
                    ("confirmed", "Confirmed"),
                    ("legacy", "Legacy package"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="handover_submitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="handover_submitted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submitted_move_in_handovers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="initial_meter_readings",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="moveinpackage",
            name="keys_issued",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_packages_legacy, migrations.RunPython.noop),
    ]
