from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0047_director_portal_persistence"),
    ]

    operations = [
        migrations.AddField(
            model_name="caretakerinvitation",
            name="code_hash",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name="caretakerinvitation",
            name="failed_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="caretakerinvitation",
            name="email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="caretakerinvitation",
            name="email_delivery_error",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="tenantactivationchallenge",
            name="failed_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="tenantactivationchallenge",
            name="email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenantactivationchallenge",
            name="email_delivery_error",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
