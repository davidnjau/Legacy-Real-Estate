import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0045_agencymembership_is_active_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="LandlordActivationChallenge",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("code_hash", models.CharField(max_length=256)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("landlord", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="activation_challenges", to="users.landlord")),
            ],
            options={
                "indexes": [models.Index(fields=["landlord", "expires_at"], name="users_landl_landlor_bd7da0_idx")],
            },
        ),
    ]
