import django.conf
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0072_conversationstate_active_property"),
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantactivationchallenge",
            name="existing_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tenant_account_link_challenges",
                to=django.conf.settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="landlordactivationchallenge",
            name="existing_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="landlord_account_link_challenges",
                to=django.conf.settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="staffactivationchallenge",
            name="existing_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="staff_account_link_challenges",
                to=django.conf.settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="staffactivationchallenge",
            name="pending_roles",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="staffactivationchallenge",
            name="pending_branch_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
