from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_existing_caretaker_assignments(apps, schema_editor):
    PropertyCaretakerAssignment = apps.get_model("users", "PropertyCaretakerAssignment")
    through = apps.get_model("users", "Property")._meta.get_field("caretakers").remote_field.through
    for relation in through.objects.all().iterator():
        PropertyCaretakerAssignment.objects.get_or_create(
            property_id=relation.property_id,
            caretaker_id=relation.user_id,
            defaults={"assigned_by_id": relation.user_id},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0024_depositaccount_depositentry_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PropertyCaretakerAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_caretaker_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "caretaker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="property_caretaker_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "ended_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ended_caretaker_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "property",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="caretaker_assignments",
                        to="users.property",
                    ),
                ),
            ],
            options={
                "ordering": ["-assigned_at", "-id"],
                "indexes": [
                    models.Index(fields=["caretaker", "ended_at"], name="users_prope_caretak_f73cf3_idx"),
                    models.Index(fields=["property", "ended_at"], name="users_prope_propert_111e1a_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(ended_at__isnull=True),
                        fields=("property", "caretaker"),
                        name="users_active_property_caretaker_unique",
                    )
                ],
            },
        ),
        migrations.RunPython(copy_existing_caretaker_assignments, migrations.RunPython.noop),
    ]
