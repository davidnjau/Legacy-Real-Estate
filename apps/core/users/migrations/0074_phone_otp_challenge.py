import django.conf
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0073_multi_role_activation_link"),
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PhoneOTPChallenge",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("phone", models.CharField(max_length=12)),
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("login", "Portal login"),
                            ("tenant_activation", "Tenant activation"),
                            ("landlord_activation", "Landlord activation"),
                            ("whatsapp_verification", "WhatsApp verification"),
                        ],
                        max_length=32,
                    ),
                ),
                ("code_hash", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "landlord",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="phone_otp_challenges",
                        to="users.landlord",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="phone_otp_challenges",
                        to="users.tenant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="phone_otp_challenges",
                        to=django.conf.settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["phone", "purpose", "created_at"],
                        name="users_phoneotp_phone_purpose",
                    ),
                    models.Index(
                        fields=["user", "purpose", "expires_at"],
                        name="users_phoneotp_user_purpose",
                    ),
                ],
            },
        ),
    ]
