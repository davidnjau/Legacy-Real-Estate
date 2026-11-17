import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_initial_agency_and_memberships(apps, schema_editor):
    database = schema_editor.connection.alias
    Agency = apps.get_model("users", "Agency")
    AgencyMembership = apps.get_model("users", "AgencyMembership")
    Branch = apps.get_model("users", "Branch")
    UserProfile = apps.get_model("users", "UserProfile")
    agency, _ = Agency.objects.using(database).get_or_create(
        slug="legacy-real-estate",
        defaults={"name": "Legacy Real Estate"},
    )
    Branch.objects.using(database).filter(agency__isnull=True).update(agency=agency)

    for profile in UserProfile.objects.using(database).select_related("user").iterator():
        membership, _ = AgencyMembership.objects.using(database).get_or_create(
            profile_id=profile.pk,
            agency_id=agency.pk,
        )
        membership.roles.set(profile.user.groups.using(database).all())
        membership.branches.set(
            profile.branches.using(database).filter(agency_id=agency.pk)
        )


class Migration(migrations.Migration):

    dependencies = [  # noqa: RUF012
        ("users", "0042_tenant_is_provisional_unit_is_migrated_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [  # noqa: RUF012
        migrations.CreateModel(
            name="Agency",
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
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.CreateModel(
            name="AgencyMembership",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "agency",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="memberships",
                        to="users.agency",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agency_memberships",
                        to="users.userprofile",
                    ),
                ),
                (
                    "roles",
                    models.ManyToManyField(
                        blank=True,
                        related_name="agency_memberships",
                        to="auth.group",
                    ),
                ),
                (
                    "branches",
                    models.ManyToManyField(
                        blank=True,
                        related_name="agency_memberships",
                        to="users.branch",
                    ),
                ),
            ],
            options={"ordering": ["agency__name", "profile__name"]},
        ),
        migrations.AddField(
            model_name="branch",
            name="agency",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="branches",
                to="users.agency",
            ),
        ),
        migrations.RunPython(create_initial_agency_and_memberships, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="branch",
            name="name",
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name="branch",
            name="agency",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="branches",
                to="users.agency",
            ),
        ),
        migrations.AddConstraint(
            model_name="branch",
            constraint=models.UniqueConstraint(
                fields=("agency", "name"),
                name="users_branch_agency_name_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="agencymembership",
            constraint=models.UniqueConstraint(
                fields=("profile", "agency"),
                name="users_agencymembership_profile_agency_unique",
            ),
        ),
        migrations.AlterModelOptions(
            name="branch",
            options={"ordering": ["agency__name", "name"]},
        ),
    ]
