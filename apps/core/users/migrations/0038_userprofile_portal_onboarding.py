from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0037_smsdelivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="onboarding_completed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="tutorial_completed",
            field=models.BooleanField(default=False),
        ),
    ]
