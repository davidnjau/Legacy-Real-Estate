from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0074_phone_otp_challenge"),
    ]

    operations = [
        migrations.AddField(
            model_name="moveinsettlement",
            name="tenant_link_sms_queued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="moveinsettlement",
            name="tenant_link_sms_error",
            field=models.TextField(blank=True),
        ),
    ]
