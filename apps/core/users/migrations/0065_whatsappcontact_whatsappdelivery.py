import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0064_moveinpackage_caretaker_handover"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhatsAppContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("wa_id", models.CharField(max_length=20, unique=True)),
                ("id_number_hash", models.CharField(blank=True, max_length=64)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="whatsapp_contacts", to="users.tenant")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="whatsapp_contacts", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.CreateModel(
            name="WhatsAppDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("template_name", models.CharField(max_length=100)),
                ("recipient_encrypted", models.BinaryField()),
                ("payload_encrypted", models.BinaryField()),
                ("status", models.CharField(choices=[("queued", "Queued"), ("sending", "Sending"), ("sent", "Sent"), ("failed", "Failed"), ("expired", "Expired")], default="queued", max_length=20)),
                ("channel_status", models.CharField(blank=True, max_length=20)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("available_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("provider_message_id", models.CharField(blank=True, max_length=255)),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="queued_whatsapp_deliveries", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="whatsappdelivery",
            index=models.Index(fields=["status", "available_at"], name="users_whats_status_19e334_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappdelivery",
            index=models.Index(fields=["provider_message_id"], name="users_whats_provide_2a0bc8_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappdelivery",
            index=models.Index(fields=["created_by", "created_at"], name="users_whats_created_30a8be_idx"),
        ),
    ]
