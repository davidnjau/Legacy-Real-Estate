import calendar
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from openpyxl import Workbook

from users.admin import AuditedGroupAdmin, AuditLogAdmin, BranchAdmin
from users.audit import record_audit_event
from users.move_in import sync_move_in_occupancy
from users.models import (
    Agency,
    AgencyMembership,
    AgencySettings,
    Announcement,
    BillingNotification,
    ArrearsReminder,
    ArrearsPaymentPlan,
    ArrearsPolicy,
    ArrearsWaiver,
    AuditLog,
    BankStatementImport,
    BankStatementRow,
    Block,
    Branch,
    CaretakerInvitation,
    DepositAccount,
    DepositEntry,
    DirectorActivationChallenge,
    Floor,
    Invoice,
    InvoiceLine,
    InvoiceWaiverRequest,
    Landlord,
    LandlordActivationChallenge,
    LandlordRemittance,
    MaintenanceCostAllocation,
    MaintenanceQuotation,
    MaintenanceTicket,
    MaintenanceTicketComment,
    ManagementAgreement,
    MeterReading,
    MeterReadingDraft,
    MoveInCharge,
    MoveInChecklistItem,
    MoveInPackage,
    MoveInPayment,
    MoveInSettlement,
    MoveOutNotice,
    MoveOutSettlement,
    MpesaCallback,
    Payment,
    PaymentAllocation,
    Property,
    PropertyBillingSettings,
    PropertyCaretakerAssignment,
    PropertyDocument,
    PropertyExpense,
    PropertyInspection,
    PropertyRecurringCharge,
    PropertyUnitType,
    PropertyUnitTypeUtility,
    PropertyViewing,
    Receipt,
    SMSDelivery,
    SMSLoginChallenge,
    StaffActivationChallenge,
    Tenancy,
    Tenant,
    TenantActivationChallenge,
    TenantCredit,
    TenantDocument,
    TOTPLoginChallenge,
    Unit,
    UnitCharge,
    UnitReservation,
    UserProfile,
    UtilityConfiguration,
    Vendor,
    ViewingLead,
)
from users.roles import CARETAKER_ROLE, LANDLORD_PORTAL_ROLE, SUPER_ADMIN_ROLE, TENANT_PORTAL_ROLE
from users.sms import (
    dispatch_next_sms_delivery,
    queue_template_sms,
    render_sms_template,
    send_sms,
)
from users.totp import _code_for_counter, encrypt_secret, verify_code


class HealthCheckTests(SimpleTestCase):
    def test_health_endpoint_returns_ok(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_endpoint_is_public_and_read_only(self):
        response = self.client.post("/api/v1/health")

        self.assertEqual(response.status_code, 405)

    def test_totp_verifies_rfc_6238_sha1_vector(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertTrue(verify_code(secret, "287082", timestamp=59))
        self.assertFalse(verify_code(secret, "287083", timestamp=59))


class PropertyBillingSettingsTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.get(name="Nairobi")
        self.property = Property.objects.create(
            branch=self.branch,
            name="Billing Settings Property",
            property_type="residential",
            location="Nairobi",
        )

    def test_billing_settings_validate_month_and_nonnegative_deposits(self):
        settings_record = PropertyBillingSettings(
            property=self.property,
            first_online_billing_month="2026-11",
        )
        settings_record.full_clean()
        settings_record.save()

        settings_record.universal_security_deposit = Decimal(-1)
        with self.assertRaises(ValidationError):
            settings_record.full_clean()

        settings_record.universal_security_deposit = Decimal(8000)
        settings_record.first_online_billing_month = "2026-13"
        with self.assertRaises(ValidationError):
            settings_record.full_clean()

    def test_recurring_charge_name_is_unique_case_insensitively_per_property(self):
        PropertyRecurringCharge.objects.create(
            property=self.property,
            name="Garbage",
            amount=Decimal("250.00"),
        )
        duplicate = PropertyRecurringCharge(
            property=self.property,
            name="GARBAGE",
            amount=Decimal("250.00"),
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        other_property = Property.objects.create(
            branch=self.branch,
            name="Other Billing Settings Property",
            property_type="residential",
            location="Nairobi",
        )
        PropertyRecurringCharge.objects.create(
            property=other_property,
            name="Garbage",
            amount=Decimal("300.00"),
        )


class UserAccessTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.nairobi = Branch.objects.get(name="Nairobi")
        self.nakuru = Branch.objects.get(name="Nakuru")

    def create_user(self, **kwargs):
        defaults = {
            "email": "person@example.com",
            "name": "Example Person",
            "phone": "254712345678",
        }
        defaults.update(kwargs)
        email = defaults["email"]
        user = self.user_model.objects.create_user(
            username=email,
            email=email,
            password="Long-test-passphrase-29!",
        )
        UserProfile.objects.create(
            user=user,
            name=defaults["name"],
            phone=defaults["phone"],
        )
        return user

    def test_multi_agency_memberships_keep_roles_and_branches_separate(self):
        agency = Agency.objects.create(name="Second Agency", slug="second-agency")
        second_branch = Branch.objects.create(
            agency=agency,
            name="Nairobi",
            town="Nairobi",
        )
        user = self.create_user(email="multi-agency@example.com")
        user.profile.branches.add(self.nairobi, second_branch)

        legacy_membership = AgencyMembership.objects.get(
            profile=user.profile,
            agency=self.nairobi.agency,
        )
        second_membership = AgencyMembership.objects.get(
            profile=user.profile,
            agency=agency,
        )
        self.assertEqual(
            set(legacy_membership.branches.values_list("pk", flat=True)),
            {self.nairobi.pk},
        )
        self.assertEqual(
            set(second_membership.branches.values_list("pk", flat=True)),
            {second_branch.pk},
        )
        legacy_membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        second_membership.roles.add(Group.objects.get(name="Accountant"))

        director_branches = user.profile.accessible_branches(
            role_names=(SUPER_ADMIN_ROLE,)
        )
        self.assertEqual(
            set(director_branches.values_list("agency_id", flat=True)),
            {self.nairobi.agency_id},
        )
        self.assertEqual(
            set(user.profile.accessible_branches().values_list("agency_id", flat=True)),
            {self.nairobi.agency_id, agency.pk},
        )

        third_agency = Agency.objects.create(name="Third Agency", slug="third-agency")
        foreign_branch = Branch.objects.create(
            agency=third_agency,
            name="Mombasa",
            town="Mombasa",
        )
        legacy_membership.branches.add(foreign_branch)
        self.assertNotIn(
            foreign_branch.pk,
            user.profile.accessible_branches().values_list("pk", flat=True),
        )

        user.profile.branches.set([self.nairobi])
        self.assertEqual(
            set(user.profile.accessible_branches().values_list("agency_id", flat=True)),
            {self.nairobi.agency_id},
        )

    def create_portal_landlord(self, email="landlord@example.com"):
        phone = f"25471234{UserProfile.objects.count():04d}"
        user = self.create_user(email=email, phone=phone)
        user.groups.add(Group.objects.get(name="Landlord (portal)"))
        landlord = Landlord.objects.create(
            branch=self.nairobi,
            portal_user=user,
            name="Portal Landlord",
            id_or_registration_number=f"PORTAL-{user.pk}",
            phone=phone,
            email=email,
        )
        property_obj = Property.objects.create(
            branch=self.nairobi,
            landlord=landlord,
            name=f"Portal Property {user.pk}",
            property_type="residential",
            location="Nairobi",
        )
        return user, property_obj

    def create_tenant_portal(self, suffix, active=True):
        email = f"maintenance-tenant-{suffix}@example.com"
        phone = f"2547123456{suffix}"
        user = self.create_user(email=email, phone=phone)
        user.groups.add(Group.objects.get(name="Tenant (portal)"))
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name=f"Maintenance Tenant Property {suffix}",
            property_type="Apartment",
            location="Nairobi",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code=f"MT-{suffix}",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name=f"Maintenance Tenant {suffix}",
            national_id_or_passport=f"MT-ID-{suffix}",
            phone=phone,
            email=email,
            portal_user=user,
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=timezone.localdate() - timedelta(days=30),
            status=Tenancy.STATUS_ACTIVE if active else Tenancy.STATUS_ENDED,
        )
        return user, tenant, tenancy

    @override_settings(PUBLIC_SITE_URL="https://legacy.example")
    @patch("users.management.commands.bootstrap_director._send_director_activation_email")
    def test_director_bootstrap_creates_one_time_email_activation(self, send_activation_email):
        output = StringIO()

        call_command(
            "bootstrap_director",
            agency_name="Legacy Real Estate",
            agency_slug="legacy-real-estate",
            name="Initial Director",
            email="director@example.com",
            phone="254712345679",
            authorized_by="deployment-approval-123",
            confirm="PROVISION-AGENCY-DIRECTOR",
            stdout=output,
        )

        user = self.user_model.objects.get(email="director@example.com")
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())
        membership = AgencyMembership.objects.get(profile=user.profile)
        self.assertEqual(membership.agency.slug, "legacy-real-estate")
        self.assertEqual(
            list(membership.roles.values_list("name", flat=True)), [SUPER_ADMIN_ROLE]
        )
        self.assertEqual(user.profile.name, "Initial Director")
        self.assertEqual(user.profile.phone, "254712345679")
        self.assertTrue(
            AuditLog.objects.filter(
                action="director.bootstrap_created",
                entity="User",
                new_values__user_id=user.pk,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="director.bootstrap_created",
                new_values__authorized_by="deployment-approval-123",
            ).exists()
        )
        self.assertIn("one-time activation code was sent", output.getvalue())
        challenge = DirectorActivationChallenge.objects.get(user=user)
        emailed_user, challenge_id, code = send_activation_email.call_args.args
        self.assertEqual(emailed_user, user)
        self.assertEqual(str(challenge_id), str(challenge.pk))
        self.assertTrue(challenge.expires_at > timezone.now())
        self.assertNotEqual(challenge.code_hash, code)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isascii() and code.isdigit())

        weak_password = self.client.post(
            "/api/v1/auth/director/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": code,
                "password": "123",
            },
            content_type="application/json",
        )
        self.assertEqual(weak_password.status_code, 400)
        self.assertTrue(
            DirectorActivationChallenge.objects.filter(pk=challenge.pk).exists()
        )

        activated = self.client.post(
            "/api/v1/auth/director/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": code,
                "password": "A-new-director-password-42!",
            },
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("A-new-director-password-42!"))
        self.assertFalse(DirectorActivationChallenge.objects.filter(pk=challenge.pk).exists())
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        replay = self.client.post(
            "/api/v1/auth/director/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": code,
                "password": "Another-valid-password-35!",
            },
            content_type="application/json",
        )
        self.assertEqual(replay.status_code, 400)

    @override_settings(PUBLIC_SITE_URL="https://legacy.example")
    @patch("users.management.commands.bootstrap_director._send_director_activation_email")
    def test_director_bootstrap_allows_multiple_directors_and_agencies(self, send_activation_email):
        call_command(
            "bootstrap_director",
            agency_name="Legacy Real Estate",
            agency_slug="legacy-real-estate",
            name="Initial Director",
            email="director@example.com",
            phone="254712345679",
            authorized_by="deployment-approval-123",
            confirm="PROVISION-AGENCY-DIRECTOR",
        )

        call_command(
            "bootstrap_director",
            agency_name="Second Agency",
            agency_slug="second-agency",
            name="Another Director",
            email="another-director@example.com",
            phone="254712345680",
            authorized_by="deployment-approval-124",
            confirm="PROVISION-AGENCY-DIRECTOR",
        )

        self.assertEqual(Agency.objects.count(), 2)
        self.assertEqual(AgencyMembership.objects.filter(roles__name=SUPER_ADMIN_ROLE).count(), 2)
        self.assertEqual(send_activation_email.call_count, 2)

    @override_settings(PUBLIC_SITE_URL="https://legacy.example")
    @patch(
        "users.management.commands.bootstrap_director._send_director_activation_email",
        side_effect=RuntimeError("mail service unavailable"),
    )
    def test_director_bootstrap_preserves_account_when_activation_email_fails(
        self, send_activation_email
    ):
        with self.assertRaisesMessage(CommandError, "account was created"):
            call_command(
                "bootstrap_director",
                agency_name="Legacy Real Estate",
                agency_slug="legacy-real-estate",
                name="Initial Director",
                email="director@example.com",
                phone="254712345679",
                authorized_by="deployment-approval-123",
                confirm="PROVISION-AGENCY-DIRECTOR",
            )

        user = self.user_model.objects.get(email="director@example.com")
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(DirectorActivationChallenge.objects.filter(user=user).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="director.bootstrap_email_failed",
                new_values__user_id=user.pk,
            ).exists()
        )
        send_activation_email.assert_called_once()

    @override_settings(PUBLIC_SITE_URL="https://legacy.example")
    @patch("users.management.commands.bootstrap_director._send_director_activation_email")
    def test_director_bootstrap_requires_explicit_confirmation(self, send_activation_email):
        with self.assertRaisesMessage(CommandError, "exactly PROVISION-AGENCY-DIRECTOR"):
            call_command(
                "bootstrap_director",
                agency_name="Legacy Real Estate",
                agency_slug="legacy-real-estate",
                name="Initial Director",
                email="director@example.com",
                phone="254712345679",
                authorized_by="deployment-approval-123",
                confirm="wrong",
            )

        self.assertFalse(self.user_model.objects.exists())
        send_activation_email.assert_not_called()

    @override_settings(PUBLIC_SITE_URL="https://legacy.example")
    @patch("users.management.commands.bootstrap_director._send_director_activation_email")
    def test_director_bootstrap_adds_active_user_to_another_agency_without_reset(self, send_email):
        user = self.create_user(email="existing-director@example.com")
        agency = Agency.objects.create(name="Second Agency", slug="second-agency")

        call_command(
            "bootstrap_director",
            agency_name=agency.name,
            agency_slug=agency.slug,
            name=user.profile.name,
            email=user.email,
            phone=user.profile.phone,
            authorized_by="deployment-approval-125",
            confirm="PROVISION-AGENCY-DIRECTOR",
        )

        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(
            AgencyMembership.objects.get(profile=user.profile, agency=agency)
            .roles.filter(name=SUPER_ADMIN_ROLE)
            .exists()
        )
        send_email.assert_not_called()

    @patch("users.views._send_staff_activation_email")
    def test_director_invites_staff_and_staff_activates_with_email_otp(self, send_activation_email):
        director = self.create_user(email="agency-director@example.com")
        director_membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        director_membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        foreign_agency = Agency.objects.create(name="Foreign Agency", slug="foreign-agency")
        foreign_branch = Branch.objects.create(
            agency=foreign_agency,
            name="Foreign Branch",
            town="Mombasa",
        )
        self.client.force_login(director)

        available = self.client.get(
            f"/api/v1/director/staff?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(available.status_code, 200)
        self.assertEqual(
            {branch["name"] for branch in available.json()["branches"]},
            {"Nairobi", "Nakuru"},
        )

        rejected_branch = self.client.post(
            "/api/v1/director/staff",
            data={
                "agency_id": self.nairobi.agency_id,
                "name": "New Officer",
                "email": "new-officer@example.com",
                "phone": "254712345609",
                "role": "Property Officer / Agent",
                "branch_ids": [foreign_branch.pk],
            },
            content_type="application/json",
        )
        self.assertEqual(rejected_branch.status_code, 400)
        self.assertFalse(self.user_model.objects.filter(email="new-officer@example.com").exists())

        Group.objects.filter(name="Property Officer / Agent").delete()
        invited = self.client.post(
            "/api/v1/director/staff",
            data={
                "agency_id": self.nairobi.agency_id,
                "name": "New Officer",
                "email": "new-officer@example.com",
                "phone": "254712345609",
                "role": "Property Officer / Agent",
                "branch_ids": [self.nairobi.pk, self.nakuru.pk],
            },
            content_type="application/json",
        )
        self.assertEqual(invited.status_code, 201)
        self.assertTrue(Group.objects.filter(name="Property Officer / Agent").exists())
        response_payload = invited.json()
        self.assertTrue(response_payload["activation_email_sent"])
        self.assertNotIn("code", response_payload)
        staff_user = self.user_model.objects.get(email="new-officer@example.com")
        self.assertFalse(staff_user.is_active)
        self.assertFalse(staff_user.has_usable_password())
        staff_membership = AgencyMembership.objects.get(
            profile=staff_user.profile,
            agency=self.nairobi.agency,
        )
        self.assertEqual(
            list(staff_membership.roles.values_list("name", flat=True)),
            ["Property Officer / Agent"],
        )
        self.assertEqual(
            set(staff_membership.branches.values_list("pk", flat=True)),
            {self.nairobi.pk, self.nakuru.pk},
        )
        challenge = StaffActivationChallenge.objects.get(membership=staff_membership)
        _, challenge_id, code = send_activation_email.call_args.args
        self.assertEqual(str(challenge.pk), str(challenge_id))
        self.assertNotEqual(challenge.code_hash, code)

        invalid = self.client.post(
            "/api/v1/auth/staff/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": "000000" if code != "000000" else "000001",
                "password": "A-valid-staff-password-51!",
            },
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 1)

        activated = self.client.post(
            "/api/v1/auth/staff/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": code,
                "password": "A-valid-staff-password-51!",
            },
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200)
        staff_user.refresh_from_db()
        self.assertTrue(staff_user.is_active)
        self.assertFalse(StaffActivationChallenge.objects.filter(membership=staff_membership).exists())
        self.client.logout()

        staff_login = self.client.post(
            "/api/v1/auth/login",
            data={
                "identifier": staff_user.email,
                "password": "A-valid-staff-password-51!",
            },
            content_type="application/json",
        )
        self.assertEqual(staff_login.status_code, 200)
        self.assertIn("Property Officer / Agent", staff_login.json()["user"]["roles"])

    def test_director_staff_list_is_limited_to_director_agency(self):
        director = self.create_user(email="scoped-director@example.com")
        director_membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        director_membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        other_agency = Agency.objects.create(name="Other Agency", slug="other-agency")
        self.client.force_login(director)

        forbidden = self.client.get(
            f"/api/v1/director/staff?agency_id={other_agency.pk}"
        )
        self.assertEqual(forbidden.status_code, 403)
        unauthenticated = Client().get(
            f"/api/v1/director/staff?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(unauthenticated.status_code, 401)

    def test_director_reports_and_audit_are_agency_scoped(self):
        director = self.create_user(email="director-reports@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        other_agency = Agency.objects.create(name="Reports Other Agency", slug="reports-other")
        other_branch = Branch.objects.create(
            agency=other_agency,
            name="Other Branch",
            town="Mombasa",
        )
        visible_property = Property.objects.create(
            branch=self.nairobi,
            name="Director Visible Property",
            property_type="residential",
            location="Nairobi",
        )
        Property.objects.create(
            branch=other_branch,
            name="Director Hidden Property",
            property_type="residential",
            location="Mombasa",
        )
        AuditLog.objects.create(
            agency=self.nairobi.agency,
            user=director,
            action="visible.director.test",
            entity="Test",
        )
        AuditLog.objects.create(
            agency=other_agency,
            user=director,
            action="hidden.director.test",
            entity="Test",
        )
        self.client.force_login(director)

        properties = self.client.get(
            f"/api/v1/director/properties?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(properties.status_code, 200)
        property_names = {item["name"] for item in properties.json()["properties"]}
        self.assertIn(visible_property.name, property_names)
        self.assertNotIn("Director Hidden Property", property_names)

        for endpoint in ("overview", "financials"):
            response = self.client.get(
                f"/api/v1/director/{endpoint}?agency_id={self.nairobi.agency_id}"
            )
            self.assertEqual(response.status_code, 200, response.content)

        audit = self.client.get(
            f"/api/v1/director/audit-logs?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(audit.status_code, 200)
        actions = {event["action"] for event in audit.json()["events"]}
        self.assertIn("visible.director.test", actions)
        self.assertNotIn("hidden.director.test", actions)

        forbidden = self.client.get(
            f"/api/v1/director/financials?agency_id={other_agency.pk}"
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_director_financials_include_scoped_waivers_and_uncapped_remittance_totals(self):
        director = self.create_user(email="director-financials-waivers@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        requester = self.create_user(
            email="financials-waiver-requester@example.com",
            phone="254712349800",
        )
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Financials Waiver Property",
            property_type="Apartment",
            location="Nairobi",
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Financials Waiver Tenant",
            national_id_or_passport="ID-FINANCIALS-WAIVER-1",
            phone="254712349801",
        )
        unit = Unit.objects.create(property=property_obj, code="FW-001", unit_type="Studio")
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=timezone.localdate().replace(day=1),
            due_day=5,
        )
        today = timezone.localdate()
        invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="DIRECTOR-FINANCIALS-WAIVER",
            period_start=today.replace(day=1),
            period_end=today,
            due_date=today.replace(day=1),
            total=Decimal("1000.00"),
        )
        InvoiceWaiverRequest.objects.create(
            invoice=invoice,
            amount=Decimal("100.00"),
            reason="Approved maintenance disruption credit",
            requested_by=requester,
        )
        pre_invoice_unit = Unit.objects.create(
            property=property_obj,
            code="FW-002",
            unit_type="Studio",
        )
        pre_invoice_tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Pre-invoice Waiver Tenant",
            national_id_or_passport="ID-FINANCIALS-WAIVER-2",
            phone="254712349803",
        )
        Tenancy.objects.create(
            unit=pre_invoice_unit,
            tenant=pre_invoice_tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=today.replace(day=1),
            due_day=5,
        )
        InvoiceWaiverRequest.objects.create(
            unit=pre_invoice_unit,
            period_start=today.replace(day=1),
            amount=Decimal("75.00"),
            reason="Pre-invoice maintenance credit",
            requested_by=requester,
        )
        landlord = Landlord.objects.create(
            branch=self.nairobi,
            name="Financials Remittance Landlord",
            id_or_registration_number="FINANCIALS-REMITTANCE-1",
            phone="254712349802",
        )
        remittance_rows = [
            LandlordRemittance(
                landlord=landlord,
                period_start=today.replace(day=1),
                period_end=today,
                revision=index,
                statement_snapshot={"items": []},
                collected_amount=Decimal("10.00"),
                commission_amount=Decimal("1.00"),
                expenses_amount=Decimal("0.00"),
                tax_amount=Decimal("0.50"),
                net_payout=Decimal("8.50"),
                prepared_by=requester,
            )
            for index in range(1, 202)
        ]
        LandlordRemittance.objects.bulk_create(remittance_rows)
        self.client.force_login(director)

        response = self.client.get(
            f"/api/v1/director/financials?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["summary"]["remittance_count"], 201)
        self.assertEqual(payload["summary"]["pending_approval_count"], 201)
        self.assertEqual(payload["summary"]["pending_approval"], "1708.50")
        self.assertEqual(payload["summary"]["tax_withheld"], "100.50")
        self.assertEqual(len(payload["remittances"]), 200)
        self.assertEqual(payload["pending_waiver_count"], 2)
        linked_request = next(item for item in payload["waiver_requests"] if item["invoice_id"] == invoice.pk)
        unlinked_request = next(item for item in payload["waiver_requests"] if item["invoice_id"] is None)
        self.assertEqual(linked_request["requested_amount"], "100.00")
        self.assertEqual(linked_request["tenant_name"], tenant.name)
        self.assertIsNone(unlinked_request["invoice_total"])
        self.assertEqual(unlinked_request["tenant_name"], pre_invoice_tenant.name)

        other_branch_response = self.client.get(
            f"/api/v1/director/financials?agency_id={self.nairobi.agency_id}"
            f"&branch_id={self.nakuru.pk}"
        )
        self.assertEqual(other_branch_response.status_code, 200, other_branch_response.content)
        self.assertEqual(other_branch_response.json()["waiver_requests"], [])
        self.assertEqual(other_branch_response.json()["pending_waiver_count"], 0)

    def test_director_sees_officer_saved_property_and_units_with_branch_isolation(self):
        director = self.create_user(
            email="director-officer-flow@example.com",
            phone="254712345690",
        )
        director_membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        director_membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))

        officer = self.create_user(
            email="officer-director-flow@example.com",
            phone="254712345691",
        )
        officer_membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        officer_membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        officer_membership.branches.add(self.nairobi)
        other_agency = Agency.objects.create(name="Officer Flow Other Agency", slug="officer-flow-other")
        other_branch = Branch.objects.create(
            agency=other_agency,
            name="Other Agency Branch",
            town="Mombasa",
        )
        foreign_property = Property.objects.create(
            branch=other_branch,
            name="Other Agency Property",
            property_type="Apartment",
            location="Mombasa",
        )
        self.client.force_login(officer)

        saved = self.client.post(
            "/api/v1/properties/create",
            data={
                "branch_id": self.nairobi.pk,
                "name": "Officer Saved Property",
                "property_type": "Apartment",
                "location": "Kilimani",
            },
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 201, saved.content)
        saved_property_id = saved.json()["property"]["id"]
        self.assertEqual(saved.json()["units"], [])
        saved_property = Property.objects.get(pk=saved_property_id)
        Unit.objects.create(
            property=saved_property,
            code="OSP-01",
            unit_type="One bedroom",
            bedrooms=1,
        )
        Unit.objects.create(
            property=saved_property,
            code="OSP-02",
            unit_type="Two bedroom",
            bedrooms=2,
        )

        denied_branch = self.client.post(
            "/api/v1/properties/create",
            data={
                "branch_id": self.nakuru.pk,
                "name": "Officer Out-of-Branch Property",
                "property_type": "Apartment",
                "location": "Nakuru",
            },
            content_type="application/json",
        )
        self.assertEqual(denied_branch.status_code, 404)
        self.assertFalse(
            Property.objects.filter(name="Officer Out-of-Branch Property").exists()
        )

        self.client.force_login(director)
        agency_properties = self.client.get(
            f"/api/v1/director/properties?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(agency_properties.status_code, 200, agency_properties.content)
        visible = {
            item["id"]: item
            for item in agency_properties.json()["properties"]
        }
        self.assertIn(saved_property_id, visible)
        self.assertEqual(
            [unit["code"] for unit in visible[saved_property_id]["units"]],
            ["OSP-01", "OSP-02"],
        )
        self.assertNotIn(foreign_property.pk, visible)

        branch_properties = self.client.get(
            f"/api/v1/director/properties?agency_id={self.nairobi.agency_id}"
            f"&branch_id={self.nairobi.pk}"
        )
        self.assertEqual(branch_properties.status_code, 200, branch_properties.content)
        self.assertEqual(
            [item["id"] for item in branch_properties.json()["properties"]],
            [saved_property_id],
        )
        overview = self.client.get(
            f"/api/v1/director/overview?agency_id={self.nairobi.agency_id}"
            f"&branch_id={self.nairobi.pk}"
        )
        self.assertEqual(overview.status_code, 200, overview.content)
        self.assertEqual(overview.json()["totals"]["properties"], 1)
        self.assertEqual(overview.json()["totals"]["units"], 2)

        foreign_branch = self.client.get(
            f"/api/v1/director/properties?agency_id={self.nairobi.agency_id}"
            f"&branch_id={other_branch.pk}"
        )
        self.assertEqual(foreign_branch.status_code, 404)

    def test_director_settings_validate_before_persisting_and_update_branch_policy(self):
        director = self.create_user(email="director-settings@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        other_agency = Agency.objects.create(name="Settings Other Agency", slug="settings-other")
        other_branch = Branch.objects.create(
            agency=other_agency,
            name="Other Branch",
            town="Mombasa",
        )
        previous_policy = ArrearsPolicy.objects.filter(branch=self.nairobi).first()
        previous_policy_state = (
            (previous_policy.grace_days, previous_policy.penalty_type, previous_policy.penalty_flat_fee)
            if previous_policy
            else None
        )
        self.client.force_login(director)

        rejected = self.client.patch(
            "/api/v1/director/settings",
            data=json.dumps(
                {
                    "agency_id": self.nairobi.agency_id,
                    "grace_period_days": 12,
                    "branch_paybills": {str(other_branch.pk): ["12345"]},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(
            AgencySettings.objects.get(agency=self.nairobi.agency).grace_period_days,
            5,
        )
        current_policy = ArrearsPolicy.objects.filter(branch=self.nairobi).first()
        if previous_policy_state is None:
            self.assertIsNone(current_policy)
        else:
            self.assertEqual(
                (current_policy.grace_days, current_policy.penalty_type, current_policy.penalty_flat_fee),
                previous_policy_state,
            )

        saved = self.client.patch(
            "/api/v1/director/settings",
            data=json.dumps(
                {
                    "agency_id": self.nairobi.agency_id,
                    "grace_period_days": 12,
                    "penalty_type": "flat",
                    "penalty_flat_fee": "150.00",
                    "branch_paybills": {str(self.nairobi.pk): ["12345", "54321"]},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["settings"]["grace_period_days"], 12)
        branch_payload = next(
            item for item in saved.json()["branches"] if item["id"] == self.nairobi.pk
        )
        self.assertEqual(branch_payload["paybill_numbers"], ["12345", "54321"])
        policy = ArrearsPolicy.objects.get(branch=self.nairobi)
        self.assertEqual(policy.grace_days, 12)
        self.assertEqual(policy.penalty_type, "flat")
        self.assertEqual(policy.penalty_flat_fee, Decimal("150.00"))
        audit_event = AuditLog.objects.get(
            agency=self.nairobi.agency,
            action="agency.settings_updated",
        )
        self.assertEqual(
            audit_event.new_values["branch_paybills"][str(self.nairobi.pk)],
            ["12345", "54321"],
        )

    def test_director_remittance_decisions_enforce_agency_and_maker_checker(self):
        director = self.create_user(email="director-remittance@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        preparer = self.create_user(
            email="remittance-preparer@example.com",
            phone="254712345611",
        )
        today = timezone.localdate()
        landlord = Landlord.objects.create(
            branch=self.nairobi,
            name="Director Remittance Landlord",
            id_or_registration_number="DIRECTOR-REMITTANCE-1",
            phone="254712345609",
        )
        remittance = LandlordRemittance.objects.create(
            landlord=landlord,
            period_start=today.replace(day=1),
            period_end=today,
            statement_snapshot={"items": []},
            collected_amount=Decimal("1000.00"),
            commission_amount=Decimal("100.00"),
            expenses_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            net_payout=Decimal("900.00"),
            prepared_by=preparer,
        )
        self_remittance = LandlordRemittance.objects.create(
            landlord=landlord,
            period_start=today.replace(day=1),
            period_end=today,
            revision=2,
            statement_snapshot={"items": []},
            collected_amount=Decimal("1000.00"),
            commission_amount=Decimal("100.00"),
            expenses_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            net_payout=Decimal("900.00"),
            prepared_by=director,
        )
        other_agency = Agency.objects.create(name="Remittance Other Agency", slug="remittance-other")
        other_branch = Branch.objects.create(
            agency=other_agency,
            name="Other Branch",
            town="Mombasa",
        )
        other_landlord = Landlord.objects.create(
            branch=other_branch,
            name="Other Agency Landlord",
            id_or_registration_number="OTHER-REMITTANCE-1",
            phone="254712345610",
        )
        other_remittance = LandlordRemittance.objects.create(
            landlord=other_landlord,
            period_start=today.replace(day=1),
            period_end=today,
            statement_snapshot={"items": []},
            collected_amount=Decimal("500.00"),
            commission_amount=Decimal("50.00"),
            expenses_amount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            net_payout=Decimal("450.00"),
            prepared_by=preparer,
        )
        self.client.force_login(director)

        self_approval = self.client.post(
            f"/api/v1/director/remittances/{self_remittance.pk}/decision",
            data={"agency_id": self.nairobi.agency_id, "action": "approve"},
            content_type="application/json",
        )
        self.assertEqual(self_approval.status_code, 403)
        outside_agency = self.client.post(
            f"/api/v1/director/remittances/{other_remittance.pk}/decision",
            data={"agency_id": self.nairobi.agency_id, "action": "approve"},
            content_type="application/json",
        )
        self.assertEqual(outside_agency.status_code, 404)

        approved = self.client.post(
            f"/api/v1/director/remittances/{remittance.pk}/decision",
            data={"agency_id": self.nairobi.agency_id, "action": "approve"},
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(
            approved.json()["remittance"]["status"],
            LandlordRemittance.STATUS_APPROVED,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                agency=self.nairobi.agency,
                action="landlord_remittance.approved",
            ).exists()
        )

    @patch("users.views._send_landlord_activation_email")
    def test_property_officer_invites_and_activates_landlord(self, send_activation_email):
        officer = self.create_user(email="landlord-onboarding-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        invited = self.client.post(
            "/api/v1/property-officer/landlords",
            data={
                "branch_id": self.nairobi.pk,
                "name": "New Landlord",
                "email": "new-landlord@example.com",
                "phone": "254712345606",
                "id_or_registration_number": "LANDLORD-ONBOARD-1",
                "kra_pin": "A123456789B",
            },
            content_type="application/json",
        )
        self.assertEqual(invited.status_code, 201, invited.content)
        self.assertTrue(invited.json()["activation_email_sent"])
        landlord = Landlord.objects.get(id_or_registration_number="LANDLORD-ONBOARD-1")
        user = self.user_model.objects.get(email="new-landlord@example.com")
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.groups.filter(name=LANDLORD_PORTAL_ROLE).exists())
        challenge = LandlordActivationChallenge.objects.get(landlord=landlord)
        _, challenge_id, code = send_activation_email.call_args.args
        self.assertEqual(str(challenge.pk), str(challenge_id))
        self.assertNotEqual(challenge.code_hash, code)

        activated = self.client.post(
            "/api/v1/auth/landlord/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": code,
                "password": "A-valid-landlord-password-52!",
            },
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(LandlordActivationChallenge.objects.filter(landlord=landlord).exists())

        self.client.logout()
        login = self.client.post(
            "/api/v1/auth/login",
            data={
                "identifier": user.email,
                "password": "A-valid-landlord-password-52!",
            },
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn(LANDLORD_PORTAL_ROLE, login.json()["user"]["roles"])

    def test_property_officer_landlord_list_is_branch_scoped_and_resend_requires_auth(self):
        officer = self.create_user(email="landlord-scope-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        accessible = Landlord.objects.create(
            branch=self.nairobi,
            name="Accessible Landlord",
            id_or_registration_number="LANDLORD-SCOPE-1",
            phone="254712345605",
            email="accessible-landlord@example.com",
        )
        Landlord.objects.create(
            branch=self.nakuru,
            name="Other Branch Landlord",
            id_or_registration_number="LANDLORD-SCOPE-2",
            phone="254712345604",
            email="other-landlord@example.com",
        )
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Unassigned Landlord Property",
            property_type="Apartment",
            location="Nairobi",
        )

        response = Client().get(
            f"/api/v1/property-officer/landlords?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(response.status_code, 401)
        unauthenticated_resend = Client().post(
            f"/api/v1/property-officer/landlords/{accessible.pk}/resend-activation",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(unauthenticated_resend.status_code, 401)

        self.client.force_login(officer)
        listed = self.client.get(
            f"/api/v1/property-officer/landlords?agency_id={self.nairobi.agency_id}"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [row["id"] for row in listed.json()["landlords"]],
            [accessible.pk],
        )
        self.assertEqual(
            [row["id"] for row in listed.json()["properties"]],
            [property_obj.pk],
        )

    @patch("users.views._send_landlord_activation_email")
    def test_property_officer_landlord_creation_can_assign_an_unassigned_property(self, send_email):
        officer = self.create_user(email="landlord-property-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Landlord Assignment Property",
            property_type="Apartment",
            location="Nairobi",
        )
        self.client.force_login(officer)

        response = self.client.post(
            "/api/v1/property-officer/landlords",
            data={
                "branch_id": self.nairobi.pk,
                "property_id": property_obj.pk,
                "name": "Assigned Landlord",
                "email": "assigned-landlord@example.com",
                "phone": "254712345606",
                "id_or_registration_number": "LANDLORD-PROPERTY-1",
                "kra_pin": "",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        property_obj.refresh_from_db()
        self.assertEqual(property_obj.landlord_id, response.json()["landlord"]["id"])
        send_email.assert_called_once()

        conflict = self.client.post(
            "/api/v1/property-officer/landlords",
            data={
                "branch_id": self.nairobi.pk,
                "property_id": property_obj.pk,
                "name": "Second Landlord",
                "email": "second-assigned-landlord@example.com",
                "phone": "254712345607",
                "id_or_registration_number": "LANDLORD-PROPERTY-2",
                "kra_pin": "",
            },
            content_type="application/json",
        )
        self.assertEqual(conflict.status_code, 409, conflict.content)
        self.assertFalse(
            Landlord.objects.filter(
                branch=self.nairobi,
                id_or_registration_number="LANDLORD-PROPERTY-2",
            ).exists()
        )

    def test_property_officer_leads_and_viewings_are_persisted_and_branch_scoped(self):
        officer = self.create_user(email="viewing-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Viewing Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="VIEW-01", unit_type="Studio")
        foreign_property = Property.objects.create(
            branch=self.nakuru,
            name="Foreign Viewing Property",
            property_type="Apartment",
            location="Nakuru",
        )
        foreign_lead = ViewingLead.objects.create(
            branch=self.nakuru,
            property=foreign_property,
            full_name="Out of scope",
            phone="254700000001",
            created_by=officer,
        )
        foreign_viewing = PropertyViewing.objects.create(
            lead=foreign_lead,
            scheduled_for=timezone.now(),
            scheduled_by=officer,
        )
        self.client.force_login(officer)

        created = self.client.post(
            "/api/v1/property-officer/leads",
            data={
                "branch_id": self.nairobi.pk,
                "property_id": property_obj.pk,
                "unit_id": unit.pk,
                "full_name": "Caroline Mwende",
                "phone": "254712345678",
                "email": "caroline@example.com",
                "preference": "Studio near Kilimani",
                "budget": "18000",
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        lead_id = created.json()["lead"]["id"]
        self.assertEqual(ViewingLead.objects.get(pk=lead_id).created_by, officer)
        self.assertEqual(created.json()["lead"]["status"], ViewingLead.STATUS_NEW)

        denied_foreign_branch = self.client.post(
            "/api/v1/property-officer/leads",
            data={
                "branch_id": self.nakuru.pk,
                "full_name": "Out of scope",
                "phone": "254700000002",
            },
            content_type="application/json",
        )
        self.assertEqual(denied_foreign_branch.status_code, 404)
        listed = self.client.get("/api/v1/property-officer/leads")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["id"] for row in listed.json()["leads"]], [lead_id])

        scheduled = self.client.post(
            "/api/v1/property-officer/viewings",
            data={
                "lead_id": lead_id,
                "scheduled_for": "2026-11-12T10:00:00Z",
                "assigned_to": officer.pk,
                "notes": "Bring the property access keys.",
            },
            content_type="application/json",
        )
        self.assertEqual(scheduled.status_code, 201, scheduled.content)
        viewing_id = scheduled.json()["viewing"]["id"]
        self.assertEqual(PropertyViewing.objects.get(pk=viewing_id).assigned_to, officer)
        self.assertEqual(
            ViewingLead.objects.get(pk=lead_id).status,
            ViewingLead.STATUS_VIEWING_BOOKED,
        )
        listed_viewings = self.client.get("/api/v1/property-officer/viewings")
        self.assertEqual(listed_viewings.status_code, 200)
        self.assertEqual([row["id"] for row in listed_viewings.json()["viewings"]], [viewing_id])
        self.assertEqual(
            self.client.patch(
                f"/api/v1/property-officer/viewings/{foreign_viewing.pk}",
                data={"status": PropertyViewing.STATUS_CANCELLED},
                content_type="application/json",
            ).status_code,
            404,
        )

        completed = self.client.patch(
            f"/api/v1/property-officer/viewings/{viewing_id}",
            data={
                "status": PropertyViewing.STATUS_COMPLETED,
                "scheduled_for": "2026-11-13T11:00:00Z",
                "notes": "Viewing completed.",
            },
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        self.assertEqual(completed.json()["viewing"]["status"], PropertyViewing.STATUS_COMPLETED)
        self.assertEqual(
            completed.json()["viewing"]["scheduled_for"],
            "2026-11-13T11:00:00+00:00",
        )
        applied = self.client.post(
            f"/api/v1/property-officer/leads/{lead_id}/status",
            data={"status": ViewingLead.STATUS_APPLIED},
            content_type="application/json",
        )
        self.assertEqual(applied.status_code, 200, applied.content)
        self.assertEqual(applied.json()["lead"]["status"], ViewingLead.STATUS_APPLIED)
        self.assertTrue(AuditLog.objects.filter(action="viewing_lead.created").exists())
        self.assertTrue(AuditLog.objects.filter(action="property_viewing.created").exists())

    def test_property_officer_import_preview_is_read_only_and_commit_is_idempotent(self):
        officer = self.create_user(email="legacy-import-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        def property_file():
            return SimpleUploadedFile(
                "properties.csv",
                b"name,property_type,location\nImported Property,Apartment,Kilimani\n",
                content_type="text/csv",
            )

        preview = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "branch_id": str(self.nairobi.pk),
                "commit": "false",
                "files": property_file(),
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["mode"], "preview")
        self.assertEqual(preview.json()["created"]["properties"], 1)
        self.assertFalse(Property.objects.filter(name="Imported Property").exists())

        committed = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "branch_id": str(self.nairobi.pk),
                "commit": "true",
                "files": property_file(),
            },
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        self.assertEqual(committed.json()["mode"], "committed")
        self.assertEqual(Property.objects.filter(name="Imported Property").count(), 1)

        repeated = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "branch_id": str(self.nairobi.pk),
                "commit": "true",
                "files": property_file(),
            },
        )
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.json()["created"]["properties"], 0)
        self.assertEqual(len(repeated.json()["skipped"]), 1)

    def test_property_officer_property_registration_stores_defaults_without_creating_units(self):
        officer = self.create_user(email="property-register-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        response = self.client.post(
            "/api/v1/properties/create",
            data=json.dumps(
                {
                    "branch_id": self.nairobi.pk,
                    "name": "Registered Property",
                    "property_type": "Apartment",
                    "location": "Nairobi",
                    "expected_unit_count": 12,
                    "default_unit_type": "Studio",
                    "default_rent": "12500.00",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        property_obj = Property.objects.get(name="Registered Property")
        self.assertEqual(property_obj.expected_unit_count, 12)
        self.assertEqual(property_obj.default_unit_type, "Studio")
        self.assertEqual(property_obj.default_rent, Decimal("12500.00"))
        self.assertEqual(
            property_obj.unit_types.values_list("name", "unit_count").get(),
            ("Studio", 12),
        )
        self.assertEqual(response.json()["property"]["unit_types"][0]["unit_count"], 12)
        self.assertFalse(Unit.objects.filter(property=property_obj).exists())
        self.assertEqual(response.json()["units"], [])

        with_units = self.client.post(
            "/api/v1/properties/create",
            data=json.dumps(
                {
                    "branch_id": self.nairobi.pk,
                    "name": "Generated Units Property",
                    "property_type": "Apartment",
                    "location": "Nairobi",
                    "units": [{"code": "A-01", "unit_type": "Studio"}],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(with_units.status_code, 400, with_units.content)
        self.assertFalse(Property.objects.filter(name="Generated Units Property").exists())

    def test_property_registration_saves_multiple_unit_types_and_utility_defaults(self):
        officer = self.create_user(email="property-mixed-types-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        response = self.client.post(
            "/api/v1/properties/create",
            data=json.dumps(
                {
                    "branch_id": self.nairobi.pk,
                    "name": "Mixed Unit Property",
                    "property_type": "Apartment",
                    "location": "Nairobi",
                    "expected_unit_count": 24,
                    "rent_due_day": 5,
                    "house_rules": {
                        "statements": [
                            "Keep shared areas clear.",
                            "Report maintenance issues promptly.",
                        ]
                    },
                    "unit_types": [
                        {
                            "name": "Bedsitter",
                            "unit_count": 14,
                            "monthly_rent": "8000.00",
                            "security_deposit": "8000.00",
                            "utilities": [
                                {
                                    "utility_type": "water",
                                    "name": "Water",
                                    "billing_method": "metered",
                                    "rate_per_unit": "125.5000",
                                    "deposit_amount": "500.00",
                                }
                            ],
                        },
                        {
                            "name": "One bedroom",
                            "unit_count": 10,
                            "monthly_rent": "12500.00",
                            "security_deposit": "12500.00",
                            "utilities": [
                                {
                                    "utility_type": "other",
                                    "name": "Garbage",
                                    "billing_method": "fixed",
                                    "fixed_amount": "300.00",
                                }
                            ],
                        },
                    ],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        property_obj = Property.objects.get(name="Mixed Unit Property")
        self.assertEqual(property_obj.expected_unit_count, 24)
        self.assertEqual(property_obj.default_unit_type, "Bedsitter")
        self.assertEqual(property_obj.default_rent, Decimal("8000.00"))
        self.assertEqual(property_obj.default_water_rate, Decimal("125.5000"))
        self.assertEqual(property_obj.water_deposit, Decimal("500.00"))
        self.assertEqual(
            property_obj.house_rules["statements"],
            [
                "Keep shared areas clear.",
                "Report maintenance issues promptly.",
            ],
        )
        self.assertFalse(Unit.objects.filter(property=property_obj).exists())
        self.assertEqual(
            list(property_obj.unit_types.values_list("name", "monthly_rent")),
            [
                ("Bedsitter", Decimal("8000.00")),
                ("One bedroom", Decimal("12500.00")),
            ],
        )
        self.assertEqual(
            list(property_obj.unit_types.values_list("name", "unit_count")),
            [("Bedsitter", 14), ("One bedroom", 10)],
        )
        self.assertEqual(
            [unit_type["unit_count"] for unit_type in response.json()["property"]["unit_types"]],
            [14, 10],
        )
        water = PropertyUnitTypeUtility.objects.get(
            unit_type__property=property_obj,
            utility_type=PropertyUnitTypeUtility.UTILITY_WATER,
        )
        self.assertEqual(water.billing_method, PropertyUnitTypeUtility.METHOD_METERED)
        self.assertEqual(water.rate_per_unit, Decimal("125.5000"))
        self.assertEqual(water.deposit_amount, Decimal("500.00"))
        self.assertEqual(len(response.json()["property"]["unit_types"]), 2)
        self.assertTrue(response.json()["property"]["has_water_utility"])

        mismatched_counts = self.client.post(
            "/api/v1/properties/create",
            data=json.dumps(
                {
                    "branch_id": self.nairobi.pk,
                    "name": "Mismatched Unit Counts Property",
                    "property_type": "Apartment",
                    "location": "Nairobi",
                    "expected_unit_count": 24,
                    "unit_types": [
                        {
                            "name": "Bedsitter",
                            "unit_count": 14,
                            "monthly_rent": "8000.00",
                        },
                        {
                            "name": "One bedroom",
                            "unit_count": 9,
                            "monthly_rent": "12500.00",
                        },
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(mismatched_counts.status_code, 400, mismatched_counts.content)
        self.assertTrue(
            mismatched_counts.json()["type"].endswith("/invalid-property-unit-counts")
        )
        self.assertFalse(
            Property.objects.filter(name="Mismatched Unit Counts Property").exists()
        )

        invalid_response = self.client.post(
            "/api/v1/properties/create",
            data=json.dumps(
                {
                    "branch_id": self.nairobi.pk,
                    "name": "Invalid Metered Utility Property",
                    "property_type": "Apartment",
                    "location": "Nairobi",
                    "expected_unit_count": 1,
                    "unit_types": [
                        {
                            "name": "Studio",
                            "unit_count": 1,
                            "monthly_rent": "9000.00",
                            "utilities": [
                                {
                                    "utility_type": "water",
                                    "name": "Water",
                                    "billing_method": "metered",
                                }
                            ],
                        }
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(invalid_response.status_code, 400, invalid_response.content)
        self.assertFalse(Property.objects.filter(name="Invalid Metered Utility Property").exists())
        self.assertFalse(
            PropertyUnitType.objects.filter(
                property__name="Invalid Metered Utility Property"
            ).exists()
        )

    def test_property_scoped_import_creates_units_and_rent_without_creating_properties(self):
        officer = self.create_user(email="property-unit-import@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)
        property_count = Property.objects.filter(branch=self.nairobi).count()
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Scoped Import Property",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyUnitType.objects.create(
            property=property_obj,
            name="Studio",
            monthly_rent=Decimal("12500.00"),
        )

        def unit_file():
            return SimpleUploadedFile(
                "units.csv",
                b"unit_code,unit_type,occupancy_status,bedrooms\nA-01,Studio,Vacant,0\n",
                content_type="text/csv",
            )

        preview = self.client.post(
            "/api/v1/property-officer/imports",
            data={"property_id": str(property_obj.pk), "commit": "false", "files": unit_file()},
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["created"]["units"], 1)
        self.assertFalse(Unit.objects.filter(property=property_obj).exists())
        self.assertEqual(Property.objects.filter(branch=self.nairobi).count(), property_count + 1)

        committed = self.client.post(
            "/api/v1/property-officer/imports",
            data={"property_id": str(property_obj.pk), "commit": "true", "files": unit_file()},
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        unit = Unit.objects.get(property=property_obj, code="A-01")
        rent_charge = UnitCharge.objects.get(unit=unit, charge_type=UnitCharge.CHARGE_RENT)
        self.assertEqual(rent_charge.amount, Decimal("12500.00"))
        self.assertEqual(unit.status, Unit.STATUS_VACANT)
        self.assertEqual(unit.unit_type_definition.name, "Studio")
        self.assertEqual(Property.objects.filter(branch=self.nairobi).count(), property_count + 1)

    @patch("users.views._send_tenant_activation_email")
    def test_property_scoped_import_applies_unit_type_and_migration_balances(self, send_email):
        officer = self.create_user(email="property-ledger-import@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Ledger Migration Property",
            property_type="Apartment",
            location="Nairobi",
        )
        unit_type = PropertyUnitType.objects.create(
            property=property_obj,
            name="One bedroom",
            monthly_rent=Decimal("12500.00"),
            security_deposit=Decimal("12500.00"),
        )
        PropertyUnitTypeUtility.objects.create(
            unit_type=unit_type,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("100.0000"),
            deposit_amount=Decimal("500.00"),
        )
        PropertyUnitTypeUtility.objects.create(
            unit_type=unit_type,
            utility_type=UtilityConfiguration.UTILITY_OTHER,
            name="Garbage",
            billing_method=UtilityConfiguration.METHOD_FIXED,
            fixed_amount=Decimal("300.00"),
        )

        def uploads():
            return [
                SimpleUploadedFile(
                    "units.csv",
                    (
                        "unit_code,unit_type,occupancy_status,unit_label,block_name,floor_name,floor_level,"
                        "water_meter_no,kplc_meter_no,water_current_reading\n"
                        "LM-01,One bedroom,Occupied,Flat 1,Block A,First floor,1,W-01,K-01,20\n"
                        "LM-02,One bedroom,Notice Given,Flat 2,Block A,First floor,1,W-02,,30\n"
                    ).encode(),
                    content_type="text/csv",
                ),
                SimpleUploadedFile(
                    "tenancies.csv",
                    (
                        "unit_code,tenant_name,tenant_phone,tenant_email,tenant_id_number,tenant_kra_pin,"
                        "tenancy_start_date,expected_move_out_date,opening_arrears,arrears_as_of\n"
                        "LM-01,Ledger Tenant,254712345680,ledger.tenant@example.com,ID-LM-1,A012345678Z,"
                        "2025-01-01,,8000,2026-10-31\n"
                        "LM-02,Notice Tenant,254712345681,notice.tenant@example.com,ID-LM-2,,"
                        "2025-01-01,2026-12-15,,\n"
                    ).encode(),
                    content_type="text/csv",
                ),
            ]

        missing_water = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "false",
                "files": SimpleUploadedFile(
                    "units.csv",
                    b"unit_code,unit_type,occupancy_status\nLM-00,One bedroom,Vacant\n",
                    content_type="text/csv",
                ),
            },
        )
        self.assertEqual(missing_water.status_code, 400, missing_water.content)
        self.assertIn("water_meter_no", missing_water.json()["errors"][0]["message"])

        preview = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "false",
                "files": uploads(),
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["created"]["units"], 2)
        self.assertEqual(preview.json()["created"]["tenancies"], 2)
        self.assertEqual(preview.json()["created"]["opening_arrears"], 1)
        self.assertEqual(preview.json()["created"]["meter_readings"], 0)
        self.assertFalse(Unit.objects.filter(property=property_obj).exists())
        self.assertFalse(Tenant.objects.filter(email="ledger.tenant@example.com").exists())
        send_email.assert_not_called()

        missing_notice_tenancy = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "false",
                "files": SimpleUploadedFile(
                    "units.csv",
                    (
                        "unit_code,unit_type,occupancy_status,water_meter_no,water_current_reading\n"
                        "LM-03,One bedroom,Notice Given,W-03,40\n"
                    ).encode(),
                    content_type="text/csv",
                ),
            },
        )
        self.assertEqual(missing_notice_tenancy.status_code, 400, missing_notice_tenancy.content)
        self.assertIn("Notice Given", missing_notice_tenancy.json()["errors"][0]["message"])

        move_out_date_on_occupied = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "false",
                "files": [
                    SimpleUploadedFile(
                        "units.csv",
                        (
                            "unit_code,unit_type,occupancy_status,water_meter_no,water_current_reading\n"
                            "LM-04,One bedroom,Occupied,W-04,50\n"
                        ).encode(),
                        content_type="text/csv",
                    ),
                    SimpleUploadedFile(
                        "tenancies.csv",
                        b"unit_code,expected_move_out_date\nLM-04,2026-12-15\n",
                        content_type="text/csv",
                    ),
                ],
            },
        )
        self.assertEqual(move_out_date_on_occupied.status_code, 400, move_out_date_on_occupied.content)
        self.assertIn(
            "only valid for a Notice Given unit",
            move_out_date_on_occupied.json()["errors"][0]["message"],
        )

        committed = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": uploads(),
            },
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        unit = Unit.objects.get(property=property_obj, code="LM-01")
        notice_unit = Unit.objects.get(property=property_obj, code="LM-02")
        self.assertEqual(unit.characteristics["room_name"], "Flat 1")
        self.assertEqual(unit.floor.name, "First floor")
        self.assertEqual(unit.unit_type_definition, unit_type)
        self.assertEqual(
            unit.meters,
            {"water": "W-01", "water_reading": "20", "electricity": "K-01"},
        )
        self.assertEqual(notice_unit.status, Unit.STATUS_NOTICE_GIVEN)
        self.assertEqual(
            notice_unit.meters,
            {"water": "W-02", "water_reading": "30"},
        )
        self.assertTrue(Tenancy.objects.filter(unit=notice_unit).exists())
        self.assertEqual(
            UnitCharge.objects.get(unit=unit, charge_code="rent").amount,
            Decimal("12500.00"),
        )
        self.assertEqual(
            UnitCharge.objects.get(unit=unit, charge_code="rent").effective_from,
            date(2025, 1, 1),
        )
        self.assertEqual(
            UnitCharge.objects.get(unit=unit, charge_code__startswith="utility-").amount,
            Decimal("300.00"),
        )
        utility = UtilityConfiguration.objects.get(unit=unit, utility_type="water")
        self.assertEqual(utility.metadata["meter"], "W-01")
        self.assertFalse(MeterReading.objects.filter(unit__in=[unit, notice_unit]).exists())
        tenancy = Tenancy.objects.get(unit=unit)
        self.assertEqual(tenancy.status, Tenancy.STATUS_ACTIVE)
        self.assertEqual(tenancy.base_water_meter_reading, Decimal("20"))
        self.assertEqual(tenancy.tenant.kra_pin, "A012345678Z")
        arrears_invoice = Invoice.objects.get(tenancy=tenancy)
        self.assertEqual(arrears_invoice.total, Decimal("8000"))
        self.assertEqual(arrears_invoice.lines.get().metadata["as_of"], "2026-10-31")
        notice_tenancy = Tenancy.objects.get(unit=notice_unit)
        self.assertEqual(notice_tenancy.status, Tenancy.STATUS_ACTIVE)
        self.assertEqual(notice_tenancy.end_date, date(2026, 12, 15))
        deposit = DepositAccount.objects.get(tenancy=tenancy)
        self.assertEqual(deposit.target_amount, Decimal("13000.00"))
        self.assertEqual(deposit.balance, Decimal("0.00"))
        self.assertFalse(Payment.objects.filter(tenant=tenancy.tenant).exists())
        self.assertEqual(send_email.call_count, 2)

    def test_property_scoped_import_rejects_unregistered_types_and_payment_sheets(self):
        officer = self.create_user(email="property-import-validation@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Strict Import Property",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyUnitType.objects.create(
            property=property_obj,
            name="Bedsitter",
            monthly_rent=Decimal("8000.00"),
        )
        units_file = SimpleUploadedFile(
            "units.csv",
            b"unit_code,unit_type,occupancy_status\nSI-01,Studio,Vacant\n",
            content_type="text/csv",
        )
        wrong_type = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": units_file,
            },
        )
        self.assertEqual(wrong_type.status_code, 400, wrong_type.content)
        self.assertIn("saved property definition", wrong_type.json()["errors"][0]["message"])
        self.assertFalse(Unit.objects.filter(property=property_obj).exists())

        supported_units = SimpleUploadedFile(
            "units.csv",
            b"unit_code,unit_type,occupancy_status\nSI-01,Bedsitter,Vacant\n",
            content_type="text/csv",
        )
        payment_file = SimpleUploadedFile(
            "payments.csv",
            b"unit_code,reference,amount,source,received_at\nSI-01,OLD-1,500,cash,2026-10-01T10:00:00Z\n",
            content_type="text/csv",
        )
        unsupported_sheet = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": [supported_units, payment_file],
            },
        )
        self.assertEqual(unsupported_sheet.status_code, 400, unsupported_sheet.content)
        self.assertIn("payments", unsupported_sheet.json()["errors"][0]["sheet"])
        self.assertFalse(Unit.objects.filter(property=property_obj).exists())

    def test_property_scoped_import_reads_two_sheet_xlsx_workbook(self):
        officer = self.create_user(email="property-xlsx-import@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Two Sheet Import Property",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyUnitType.objects.create(
            property=property_obj,
            name="Bedsitter",
            monthly_rent=Decimal("8000.00"),
        )
        workbook = Workbook()
        units = workbook.active
        units.title = "Units"
        units.append(["unit_code", "unit_type", "occupancy_status", "unit_label"])
        units.append(["XLS-01", "Bedsitter", "Vacant", "Ground floor unit"])
        tenancies = workbook.create_sheet("Tenancies")
        tenancies.append([
            "unit_code",
            "tenant_name",
            "tenant_phone",
            "tenant_email",
            "tenancy_start_date",
        ])
        buffer = BytesIO()
        workbook.save(buffer)
        response = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": SimpleUploadedFile(
                    "inventory.xlsx",
                    buffer.getvalue(),
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        unit = Unit.objects.get(property=property_obj, code="XLS-01")
        self.assertEqual(unit.characteristics["room_name"], "Ground floor unit")
        self.assertFalse(Tenancy.objects.filter(unit=unit).exists())

    @patch("users.views._send_tenant_activation_email")
    def test_property_scoped_import_provisions_and_emails_unique_tenant_activations(self, send_email):
        manager = self.create_user(email="tenant-import-manager@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=manager.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(manager)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Tenant Activation Import",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyUnitType.objects.create(
            property=property_obj,
            name="Studio",
            monthly_rent=Decimal("12000.00"),
        )
        PropertyUnitType.objects.create(
            property=property_obj,
            name="One bedroom",
            monthly_rent=Decimal("15000.00"),
        )

        def unit_file():
            return SimpleUploadedFile(
                "units.csv",
                (
                    "unit_code,unit_type,occupancy_status\n"
                    "TA-01,Studio,Occupied\n"
                    "TA-02,One bedroom,Occupied\n"
                ).encode(),
                content_type="text/csv",
            )

        def tenancy_file():
            return SimpleUploadedFile(
                "tenancies.csv",
                (
                    "unit_code,tenant_name,tenant_phone,tenant_email,tenant_id_number,tenancy_start_date\n"
                    "TA-01,Imported Tenant One,254712345681,tenant.one@example.com,ID-TA-1,2026-01-01\n"
                    "TA-02,Imported Tenant Two,254712345682,tenant.two@example.com,ID-TA-2,2026-01-01\n"
                ).encode(),
                content_type="text/csv",
            )

        preview = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "false",
                "files": [unit_file(), tenancy_file()],
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["activation"]["prepared"], 2)
        self.assertEqual(preview.json()["activation"]["sent"], 0)
        self.assertFalse(Tenant.objects.filter(email__in=[
            "tenant.one@example.com",
            "tenant.two@example.com",
        ]).exists())
        send_email.assert_not_called()

        Group.objects.filter(name=TENANT_PORTAL_ROLE).delete()
        committed = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": [unit_file(), tenancy_file()],
            },
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        self.assertEqual(committed.json()["activation"], {
            "prepared": 2,
            "sent": 2,
            "failed": [],
        })
        self.assertEqual(send_email.call_count, 2)

        tenants = list(
            Tenant.objects.filter(email__in=[
                "tenant.one@example.com",
                "tenant.two@example.com",
            ]).select_related("portal_user").order_by("email")
        )
        self.assertEqual(len(tenants), 2)
        self.assertEqual(len({tenant.portal_user_id for tenant in tenants}), 2)
        self.assertTrue(all(not tenant.portal_user.is_active for tenant in tenants))
        self.assertTrue(all(not tenant.portal_user.has_usable_password() for tenant in tenants))
        self.assertTrue(all(
            tenant.portal_user.groups.filter(name=TENANT_PORTAL_ROLE).exists()
            for tenant in tenants
        ))

        issued = {}
        for call in send_email.call_args_list:
            email = call.args[1]
            activation_url = call.args[-2]
            activation_code = call.args[-1]
            token = activation_url.split("token=", 1)[1]
            context = self.client.get(
                f"/api/v1/tenant-activation/context?token={token}"
            )
            self.assertEqual(context.status_code, 200, context.content)
            self.assertEqual(context.json()["email"], email)
            challenge = TenantActivationChallenge.objects.get(
                tenant__email=email,
                unit__code=call.args[2],
            )
            self.assertIsNotNone(challenge.email_sent_at)
            self.assertNotEqual(challenge.token_hash, token)
            self.assertNotEqual(challenge.code_hash, activation_code)
            issued[email] = (token, activation_code, context.json()["unit_code"])
        self.assertEqual(len({item[0] for item in issued.values()}), 2)
        self.assertEqual(len({item[1] for item in issued.values()}), 2)

        token, code, unit_code = issued["tenant.one@example.com"]
        mismatched = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={
                "token": token,
                "code": issued["tenant.two@example.com"][1],
                "password": "Tenant-secure-password-91!",
            },
            content_type="application/json",
        )
        self.assertEqual(mismatched.status_code, 400, mismatched.content)
        activated = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={
                "token": token,
                "code": code,
                "password": "Tenant-secure-password-91!",
            },
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200, activated.content)
        self.client.logout()
        login = self.client.post(
            "/api/v1/auth/login",
            data={
                "identifier": "tenant.one@example.com",
                "password": "Tenant-secure-password-91!",
            },
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        self.assertIn(TENANT_PORTAL_ROLE, login.json()["user"]["roles"])
        self.assertEqual(self.client.get("/api/v1/tenant/profile").status_code, 200)
        self.assertEqual(
            self.client.get("/api/v1/tenant/unit-status").json()["unitCode"],
            unit_code,
        )

    def test_property_scoped_occupied_import_requires_tenant_email(self):
        manager = self.create_user(email="tenant-email-required@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=manager.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(manager)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Tenant Email Required",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyUnitType.objects.create(
            property=property_obj,
            name="Studio",
            monthly_rent=Decimal("8000.00"),
        )
        upload = SimpleUploadedFile(
            "units.csv",
            (
                "unit_code,unit_type,occupancy_status\n"
                "TE-01,Studio,Occupied\n"
            ).encode(),
            content_type="text/csv",
        )
        tenancy_upload = SimpleUploadedFile(
            "tenancies.csv",
            (
                "unit_code,tenant_name,tenant_phone,tenancy_start_date\n"
                "TE-01,No Email Tenant,254712345683,2026-01-01\n"
            ).encode(),
            content_type="text/csv",
        )

        response = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": [upload, tenancy_upload],
            },
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("tenant_email", response.json()["errors"][0]["message"])
        self.assertFalse(Unit.objects.filter(property=property_obj).exists())
        self.assertFalse(Tenant.objects.filter(branch=self.nairobi, name="No Email Tenant").exists())

    @patch("users.views._send_tenant_activation_email")
    def test_property_scoped_import_reports_and_retries_activation_email_failure(self, send_email):
        manager = self.create_user(email="tenant-import-retry@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=manager.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(manager)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Tenant Activation Retry",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyUnitType.objects.create(
            property=property_obj,
            name="Studio",
            monthly_rent=Decimal("8000.00"),
        )
        send_email.side_effect = [RuntimeError("mail unavailable"), None]

        def unit_file():
            return SimpleUploadedFile(
                "units.csv",
                (
                    "unit_code,unit_type,occupancy_status\n"
                    "TR-01,Studio,Occupied\n"
                ).encode(),
                content_type="text/csv",
            )

        def tenancy_file():
            return SimpleUploadedFile(
                "tenancies.csv",
                (
                    "unit_code,tenant_name,tenant_phone,tenant_email,tenant_id_number,tenancy_start_date\n"
                    "TR-01,Retry Tenant,254712345684,retry.tenant@example.com,ID-TR-1,2026-01-01\n"
                ).encode(),
                content_type="text/csv",
            )

        first = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": [unit_file(), tenancy_file()],
            },
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["activation"]["sent"], 0)
        self.assertEqual(len(first.json()["activation"]["failed"]), 1)
        tenant = Tenant.objects.get(email="retry.tenant@example.com")
        self.assertFalse(tenant.portal_user.is_active)
        first_challenge = TenantActivationChallenge.objects.get(tenant=tenant)
        self.assertEqual(first_challenge.email_delivery_error, "RuntimeError")

        second = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "property_id": str(property_obj.pk),
                "commit": "true",
                "files": [unit_file(), tenancy_file()],
            },
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()["activation"]["sent"], 1)
        first_challenge.refresh_from_db()
        self.assertIsNotNone(first_challenge.used_at)
        self.assertEqual(
            TenantActivationChallenge.objects.filter(
                tenant=tenant,
                used_at__isnull=True,
                email_sent_at__isnull=False,
            ).count(),
            1,
        )

    def test_property_officer_import_rejects_invalid_batch_without_partial_writes(self):
        officer = self.create_user(email="legacy-import-invalid@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)
        properties = SimpleUploadedFile(
            "properties.csv",
            b"name,property_type,location\nShould Roll Back,Apartment,Kilimani\n",
            content_type="text/csv",
        )
        units = SimpleUploadedFile(
            "units_tenants.csv",
            b"property_name,unit_code,unit_type,status\nShould Roll Back,UNIT-1,Studio,Unknown\n",
            content_type="text/csv",
        )
        response = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "branch_id": str(self.nairobi.pk),
                "commit": "true",
                "files": [properties, units],
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["errors"][0]["sheet"], "units_tenants")
        self.assertFalse(Property.objects.filter(name="Should Roll Back").exists())

    def test_property_officer_import_persists_units_tenants_and_historical_ledgers(self):
        officer = self.create_user(email="legacy-import-complete@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        def csv_upload(filename, rows):
            return SimpleUploadedFile(
                filename,
                rows.encode(),
                content_type="text/csv",
            )

        uploads = [
            csv_upload(
                "properties.csv",
                "name,property_type,location\nLegacy House,Apartment,Nairobi\n",
            ),
            csv_upload(
                "units_tenants.csv",
                "property_name,unit_code,unit_type,status,tenant_name,tenant_phone,tenant_email,tenancy_start_date,rent\n"
                "Legacy House,LH-01,One bedroom,Occupied,Legacy Tenant,254712345679,legacy-tenant@example.com,2026-01-01,10000\n",
            ),
            csv_upload(
                "charges.csv",
                "property_name,unit_code,charge_type,name,amount,effective_from\n"
                "Legacy House,LH-01,recurring,Garbage,500,2026-01-01\n",
            ),
            csv_upload(
                "opening_arrears.csv",
                "property_name,unit_code,invoice_number,period_start,period_end,due_date,description,amount\n"
                "Legacy House,LH-01,LEGACY-OPEN-1,2025-12-01,2025-12-31,2025-12-05,Opening arrears,10000\n",
            ),
            csv_upload(
                "payments.csv",
                "property_name,unit_code,reference,amount,source,received_at,invoice_number\n"
                "Legacy House,LH-01,LEGACY-PAY-1,10000,mpesa,2026-01-10T10:00:00+03:00,LEGACY-OPEN-1\n",
            ),
            csv_upload(
                "meter_readings.csv",
                "property_name,unit_code,meter,period,previous_value,current_value\n"
                "Legacy House,LH-01,WATER-1,2026-01-01,10,14.5\n",
            ),
            csv_upload(
                "deposits.csv",
                "property_name,unit_code,entry_type,amount,reference,description,target_amount\n"
                "Legacy House,LH-01,receipt,20000,LEGACY-DEP-1,Opening security deposit,20000\n",
            ),
        ]
        response = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "branch_id": str(self.nairobi.pk),
                "commit": "true",
                "files": uploads,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["errors"], [])
        prop = Property.objects.get(name="Legacy House")
        unit = Unit.objects.get(property=prop, code="LH-01")
        tenant = Tenant.objects.get(phone="254712345679")
        tenancy = Tenancy.objects.get(unit=unit, tenant=tenant)
        self.assertTrue(tenant.is_provisional)
        self.assertFalse(tenant.portal_user_id)
        self.assertTrue(unit.is_migrated)
        self.assertEqual(UnitCharge.objects.filter(unit=unit).count(), 2)
        invoice = Invoice.objects.get(number="LEGACY-OPEN-1")
        payment = Payment.objects.get(reference="LEGACY-PAY-1")
        self.assertEqual(payment.tenant_id, tenant.pk)
        self.assertEqual(payment.status, Payment.STATUS_ALLOCATED)
        self.assertEqual(
            PaymentAllocation.objects.get(payment=payment, invoice=invoice).amount,
            Decimal("10000.00"),
        )
        self.assertEqual(MeterReading.objects.get(unit=unit).current_value, Decimal("14.5000"))
        account = DepositAccount.objects.get(tenancy=tenancy)
        self.assertEqual(account.balance, Decimal("20000.00"))
        self.assertEqual(DepositEntry.objects.get(account=account).reference, "LEGACY-DEP-1")

    def test_property_officer_migration_ledger_tracks_opening_arrears_and_is_branch_scoped(self):
        officer = self.create_user(email="migration-ledger-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Migration Ledger Property",
            property_type="Apartment",
            location="Nairobi",
        )
        second_property = Property.objects.create(
            branch=self.nairobi,
            name="Other Migration Ledger Property",
            property_type="Apartment",
            location="Nairobi",
        )
        foreign_property = Property.objects.create(
            branch=self.nakuru,
            name="Out of Scope Migration Property",
            property_type="Apartment",
            location="Nakuru",
        )
        arrears_date = timezone.localdate() - timedelta(days=60)

        def add_migration_balance(property_record, suffix, amount, *, paid=0, waived=0, migrated=True):
            unit = Unit.objects.create(
                property=property_record,
                code=f"ML-{suffix}",
                unit_type="Studio",
                status=Unit.STATUS_OCCUPIED,
            )
            tenant = Tenant.objects.create(
                branch=property_record.branch,
                tenant_type=Tenant.INDIVIDUAL,
                name=f"Migration Tenant {suffix}",
                national_id_or_passport=f"ID-MIGRATION-{suffix}",
                phone=f"25471234{suffix:04d}",
            )
            tenancy = Tenancy.objects.create(
                unit=unit,
                tenant=tenant,
                tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
                start_date=arrears_date,
                due_day=5,
            )
            invoice = Invoice.objects.create(
                branch=property_record.branch,
                tenancy=tenancy,
                number=f"MIG-LEDGER-{suffix}",
                period_start=arrears_date,
                period_end=arrears_date,
                due_date=arrears_date,
                total=Decimal(amount),
            )
            InvoiceLine.objects.create(
                invoice=invoice,
                charge_type="arrears",
                description="Opening arrears at migration",
                amount=Decimal(amount),
                metadata={"migration_opening_balance": migrated, "as_of": arrears_date.isoformat()},
            )
            if paid:
                payment = Payment.objects.create(
                    tenant=tenant,
                    amount=Decimal(paid),
                    reference=f"MIG-LEDGER-PAY-{suffix}",
                    source=Payment.SOURCE_MANUAL,
                    received_at=timezone.now(),
                    status=Payment.STATUS_ALLOCATED,
                    allocated_amount=Decimal(paid),
                )
                PaymentAllocation.objects.create(
                    payment=payment,
                    invoice=invoice,
                    amount=Decimal(paid),
                )
            if waived:
                ArrearsWaiver.objects.create(
                    tenancy=tenancy,
                    invoice=invoice,
                    amount=Decimal(waived),
                    reason="Approved migration ledger test waiver",
                    approved_by=officer,
                )
            return invoice

        add_migration_balance(property_obj, 1, "100.00", paid="30.00", waived="10.00")
        add_migration_balance(property_obj, 2, "50.00")
        add_migration_balance(property_obj, 3, "25.00", paid="25.00")
        add_migration_balance(second_property, 4, "80.00")
        add_migration_balance(foreign_property, 5, "200.00")
        add_migration_balance(property_obj, 6, "70.00", migrated=False)

        response = self.client.get(
            f"/api/v1/property-officer/migration-ledger?branch_id={self.nairobi.pk}"
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(len(payload["ledger"]), 4)
        self.assertEqual(payload["summary"]["opening_arrears"], "255.00")
        self.assertEqual(payload["summary"]["paid_to_arrears"], "55.00")
        self.assertEqual(payload["summary"]["waived_amount"], "10.00")
        self.assertEqual(payload["summary"]["outstanding"], "190.00")
        self.assertEqual(payload["summary"]["partial"], 1)
        self.assertEqual(payload["summary"]["unpaid"], 2)
        self.assertEqual(payload["summary"]["cleared"], 1)
        self.assertNotIn("Out of Scope Migration Property", str(payload))

        property_response = self.client.get(
            "/api/v1/property-officer/migration-ledger",
            {"branch_id": self.nairobi.pk, "property_id": property_obj.pk},
        )
        self.assertEqual(property_response.status_code, 200, property_response.content)
        self.assertEqual(len(property_response.json()["ledger"]), 3)

        partial_response = self.client.get(
            "/api/v1/property-officer/migration-ledger",
            {"branch_id": self.nairobi.pk, "property_id": property_obj.pk, "status": "partial"},
        )
        self.assertEqual(partial_response.status_code, 200, partial_response.content)
        self.assertEqual(len(partial_response.json()["ledger"]), 1)
        self.assertEqual(partial_response.json()["ledger"][0]["outstanding"], "60.00")

    def test_property_officer_monthly_ledger_itemizes_bills_and_tracks_month_payments(self):
        officer = self.create_user(email="monthly-property-ledger-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Monthly Ledger Property",
            property_type="Apartment",
            location="Nairobi",
        )
        foreign_property = Property.objects.create(
            branch=self.nakuru,
            name="Out of Scope Monthly Property",
            property_type="Apartment",
            location="Nakuru",
        )
        today = timezone.localdate()
        month_start = today.replace(day=1)
        prior_month_end = month_start - timedelta(days=1)
        prior_month_start = prior_month_end.replace(day=1)
        month_end = (
            date(today.year + 1, 1, 1) - timedelta(days=1)
            if today.month == 12
            else date(today.year, today.month + 1, 1) - timedelta(days=1)
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Monthly Ledger Tenant",
            national_id_or_passport="ID-MONTHLY-LEDGER-1",
            phone="254712340001",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="ML-001",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=prior_month_start,
            due_day=5,
        )
        prior_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="MONTHLY-LEDGER-PRIOR",
            period_start=prior_month_start,
            period_end=prior_month_end,
            due_date=prior_month_start,
            total=Decimal("1000.00"),
        )
        InvoiceLine.objects.create(
            invoice=prior_invoice,
            charge_type="rent",
            description="Prior month rent",
            amount=Decimal("1000.00"),
        )
        prior_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("200.00"),
            reference="MONTHLY-LEDGER-HISTORIC-PAYMENT",
            source=Payment.SOURCE_MANUAL,
            received_at=timezone.make_aware(datetime.combine(prior_month_end, datetime.min.time())),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("200.00"),
        )
        prior_allocation = PaymentAllocation.objects.create(
            payment=prior_payment,
            invoice=prior_invoice,
            amount=Decimal("200.00"),
        )
        PaymentAllocation.objects.filter(pk=prior_allocation.pk).update(
            created_at=timezone.make_aware(datetime.combine(prior_month_end, datetime.min.time()))
        )
        prior_waiver = ArrearsWaiver.objects.create(
            tenancy=tenancy,
            invoice=prior_invoice,
            amount=Decimal("100.00"),
            reason="Prior month waiver",
            approved_by=officer,
        )
        ArrearsWaiver.objects.filter(pk=prior_waiver.pk).update(
            approved_at=timezone.make_aware(datetime.combine(prior_month_end, datetime.min.time()))
        )

        current_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="MONTHLY-LEDGER-CURRENT",
            period_start=month_start,
            period_end=month_end,
            due_date=month_start,
            total=Decimal("1250.00"),
        )
        InvoiceLine.objects.bulk_create(
            [
                InvoiceLine(
                    invoice=current_invoice,
                    charge_type="rent",
                    description="Monthly rent",
                    amount=Decimal("500.00"),
                ),
                InvoiceLine(
                    invoice=current_invoice,
                    charge_type="utility",
                    description="Water",
                    amount=Decimal("50.00"),
                ),
                InvoiceLine(
                    invoice=current_invoice,
                    charge_type="balance",
                    description="Carried outstanding balance",
                    amount=Decimal("700.00"),
                ),
            ]
        )
        for suffix, invoice, amount in (
            ("PRIOR", prior_invoice, "300.00"),
            ("CURRENT", current_invoice, "100.00"),
        ):
            payment = Payment.objects.create(
                tenant=tenant,
                amount=Decimal(amount),
                reference=f"MONTHLY-LEDGER-{suffix}-PAYMENT",
                source=Payment.SOURCE_MANUAL,
                received_at=timezone.now(),
                status=Payment.STATUS_ALLOCATED,
                allocated_amount=Decimal(amount),
            )
            PaymentAllocation.objects.create(payment=payment, invoice=invoice, amount=Decimal(amount))
        current_waiver = ArrearsWaiver.objects.create(
            tenancy=tenancy,
            invoice=prior_invoice,
            amount=Decimal("50.00"),
            reason="Current month waiver",
            approved_by=officer,
        )
        pending_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("900.00"),
            reference="MONTHLY-LEDGER-PENDING",
            source=Payment.SOURCE_CHEQUE,
            instrument_status=Payment.INSTRUMENT_PENDING,
            received_at=timezone.now(),
        )
        PaymentAllocation.objects.create(
            payment=pending_payment,
            invoice=current_invoice,
            amount=Decimal("900.00"),
        )

        unpaid_tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Unpaid Monthly Ledger Tenant",
            national_id_or_passport="ID-MONTHLY-LEDGER-2",
            phone="254712340002",
        )
        unpaid_unit = Unit.objects.create(
            property=property_obj,
            code="ML-002",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        unpaid_tenancy = Tenancy.objects.create(
            unit=unpaid_unit,
            tenant=unpaid_tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=month_start,
            due_day=5,
        )
        unpaid_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=unpaid_tenancy,
            number="MONTHLY-LEDGER-UNPAID",
            period_start=month_start,
            period_end=month_end,
            due_date=month_start,
            total=Decimal("300.00"),
        )
        InvoiceLine.objects.create(
            invoice=unpaid_invoice,
            charge_type="rent",
            description="Monthly rent",
            amount=Decimal("300.00"),
        )
        foreign_tenant = Tenant.objects.create(
            branch=self.nakuru,
            tenant_type=Tenant.INDIVIDUAL,
            name="Foreign Monthly Ledger Tenant",
            national_id_or_passport="ID-MONTHLY-LEDGER-3",
            phone="254712340003",
        )
        foreign_unit = Unit.objects.create(
            property=foreign_property,
            code="ML-003",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        foreign_tenancy = Tenancy.objects.create(
            unit=foreign_unit,
            tenant=foreign_tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=month_start,
            due_day=5,
        )
        foreign_invoice = Invoice.objects.create(
            branch=self.nakuru,
            tenancy=foreign_tenancy,
            number="MONTHLY-LEDGER-FOREIGN",
            period_start=month_start,
            period_end=month_end,
            due_date=month_start,
            total=Decimal("900.00"),
        )
        InvoiceLine.objects.create(
            invoice=foreign_invoice,
            charge_type="rent",
            description="Monthly rent",
            amount=Decimal("900.00"),
        )

        query = f"branch_id={self.nairobi.pk}&property_id={property_obj.pk}&month={today:%Y-%m}"
        response = self.client.get(f"/api/v1/property-officer/monthly-ledger?{query}")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["summary"]["records"], 2)
        self.assertEqual(payload["summary"]["new_charges"], "850.00")
        self.assertEqual(payload["summary"]["prior_balance"], "700.00")
        self.assertEqual(payload["summary"]["expected"], "1550.00")
        self.assertEqual(payload["summary"]["paid"], "400.00")
        self.assertEqual(payload["summary"]["waived"], "50.00")
        self.assertEqual(payload["summary"]["remaining"], "1100.00")
        self.assertEqual(payload["summary"]["partial"], 1)
        self.assertEqual(payload["summary"]["unpaid"], 1)
        row = next(item for item in payload["ledger"] if item["tenancy_id"] == tenancy.pk)
        self.assertEqual(row["prior_balance"], "700.00")
        self.assertEqual(row["new_charges"], "550.00")
        self.assertEqual(row["paid"], "400.00")
        self.assertEqual(row["remaining"], "800.00")
        self.assertEqual(len(row["bill_lines"]), 2)
        self.assertEqual(len(row["payments"]), 2)
        self.assertEqual(
            row["waiver_notes"],
            [
                {
                    "invoice_number": prior_invoice.number,
                    "amount": "50.00",
                    "reason": current_waiver.reason,
                }
            ],
        )
        self.assertNotIn("Out of Scope Monthly Property", str(payload))

        unpaid_response = self.client.get(f"/api/v1/property-officer/monthly-ledger?{query}&status=unpaid")
        self.assertEqual(unpaid_response.status_code, 200, unpaid_response.content)
        self.assertEqual(len(unpaid_response.json()["ledger"]), 1)
        self.assertEqual(unpaid_response.json()["ledger"][0]["tenant_name"], unpaid_tenant.name)
        cleared_payment = Payment.objects.create(
            tenant=unpaid_tenant,
            amount=Decimal("300.00"),
            reference="MONTHLY-LEDGER-CLEARED-PAYMENT",
            source=Payment.SOURCE_MANUAL,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("300.00"),
        )
        PaymentAllocation.objects.create(
            payment=cleared_payment,
            invoice=unpaid_invoice,
            amount=Decimal("300.00"),
        )
        cleared_response = self.client.get(f"/api/v1/property-officer/monthly-ledger?{query}&status=cleared")
        self.assertEqual(cleared_response.status_code, 200, cleared_response.content)
        self.assertEqual(len(cleared_response.json()["ledger"]), 1)
        self.assertEqual(cleared_response.json()["ledger"][0]["tenant_name"], unpaid_tenant.name)
        self.assertEqual(cleared_response.json()["ledger"][0]["remaining"], "0.00")
        months_response = self.client.get(
            "/api/v1/property-officer/monthly-ledger/months",
            {"branch_id": self.nairobi.pk, "property_id": property_obj.pk},
        )
        self.assertEqual(months_response.status_code, 200, months_response.content)
        self.assertIn(today.strftime("%Y-%m"), months_response.json()["months"])
        historical_month = self.client.get(
            "/api/v1/property-officer/monthly-ledger",
            {
                "branch_id": self.nairobi.pk,
                "property_id": property_obj.pk,
                "month": prior_month_start.strftime("%Y-%m"),
            },
        )
        self.assertEqual(historical_month.status_code, 200, historical_month.content)
        historical_row = historical_month.json()["ledger"][0]
        self.assertEqual(historical_row["new_charges"], "1000.00")
        self.assertEqual(historical_row["paid"], "200.00")
        self.assertEqual(historical_row["waived"], "100.00")
        self.assertEqual(historical_row["remaining"], "700.00")

    def test_property_officer_monthly_ledger_rejects_bad_month_and_out_of_scope_branch(self):
        officer = self.create_user(email="monthly-ledger-scope-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        empty_branch_months = self.client.get(
            "/api/v1/property-officer/monthly-ledger/months",
            {"branch_id": self.nairobi.pk},
        )
        self.assertEqual(empty_branch_months.status_code, 200, empty_branch_months.content)
        self.assertEqual(empty_branch_months.json()["months"], [timezone.localdate().strftime("%Y-%m")])
        self.assertEqual(
            self.client.get("/api/v1/property-officer/monthly-ledger?month=2026-13").status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/property-officer/monthly-ledger/months?branch_id={self.nakuru.pk}"
            ).status_code,
            404,
        )

    def test_property_officer_import_reads_xlsx_sheets(self):
        officer = self.create_user(email="legacy-import-xlsx@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        workbook = Workbook()
        properties = workbook.active
        properties.title = "Properties"
        properties.append(["name", "property_type", "location"])
        properties.append(["XLSX House", "Apartment", "Nairobi"])
        units = workbook.create_sheet("Units_Tenants")
        units.append(["property_name", "unit_code", "unit_type", "status"])
        units.append(["XLSX House", "XLS-01", "Studio", "Vacant"])
        file_buffer = BytesIO()
        workbook.save(file_buffer)
        self.client.force_login(officer)
        response = self.client.post(
            "/api/v1/property-officer/imports",
            data={
                "branch_id": str(self.nairobi.pk),
                "commit": "false",
                "files": SimpleUploadedFile(
                    "legacy.xlsx",
                    file_buffer.getvalue(),
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["created"]["properties"], 1)
        self.assertEqual(response.json()["created"]["units_tenants"], 1)
        self.assertFalse(Property.objects.filter(name="XLSX House").exists())

    def test_caretaker_cannot_access_property_officer_import(self):
        caretaker = self.create_user(email="legacy-import-caretaker@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=caretaker.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Caretaker"))
        membership.branches.add(self.nairobi)
        self.client.force_login(caretaker)
        response = self.client.post(
            "/api/v1/property-officer/imports",
            data={"branch_id": str(self.nairobi.pk), "commit": "false"},
        )
        self.assertEqual(response.status_code, 403)

    @patch("users.views._send_landlord_activation_email")
    def test_property_officer_can_invite_existing_landlord_and_resend_activation(self, send_email):
        officer = self.create_user(email="landlord-existing-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        landlord = Landlord.objects.create(
            branch=self.nairobi,
            name="Existing Landlord",
            id_or_registration_number="LANDLORD-EXISTING-1",
            phone="254712345603",
            email="existing-landlord@example.com",
        )
        self.client.force_login(officer)

        invited = self.client.post(
            "/api/v1/property-officer/landlords",
            data={"landlord_id": landlord.pk},
            content_type="application/json",
        )
        self.assertEqual(invited.status_code, 201, invited.content)
        self.assertEqual(invited.json()["landlord"]["account_status"], "pending_activation")
        self.assertNotIn("code", invited.json())

        resent = self.client.post(
            f"/api/v1/property-officer/landlords/{landlord.pk}/resend-activation",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(resent.status_code, 200, resent.content)
        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(
            LandlordActivationChallenge.objects.filter(landlord=landlord).count(),
            1,
        )

    @patch(
        "users.views._send_landlord_activation_email",
        side_effect=[RuntimeError("email unavailable"), None],
    )
    def test_property_officer_can_retry_after_activation_email_failure(self, send_email):
        officer = self.create_user(email="landlord-email-retry-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        failed_invite = self.client.post(
            "/api/v1/property-officer/landlords",
            data={
                "branch_id": str(self.nairobi.pk),
                "name": "Email Retry Landlord",
                "email": "landlord-email-retry@example.com",
                "phone": "254712345602",
                "id_or_registration_number": "LANDLORD-EMAIL-RETRY-1",
            },
            content_type="application/json",
        )
        self.assertEqual(failed_invite.status_code, 503)
        landlord = Landlord.objects.get(id_or_registration_number="LANDLORD-EMAIL-RETRY-1")
        self.assertEqual(landlord.portal_user.is_active, False)
        self.assertFalse(LandlordActivationChallenge.objects.filter(landlord=landlord).exists())

        retried = self.client.post(
            f"/api/v1/property-officer/landlords/{landlord.pk}/resend-activation",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertEqual(send_email.call_count, 2)
        self.assertTrue(LandlordActivationChallenge.objects.filter(landlord=landlord).exists())

    @patch("users.views._send_staff_activation_email")
    def test_director_can_update_staff_scope_and_resend_activation(self, send_email):
        director = self.create_user(email="staff-update-director@example.com")
        director_membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        director_membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        self.client.force_login(director)
        invited = self.client.post(
            "/api/v1/director/staff",
            data={
                "agency_id": self.nairobi.agency_id,
                "name": "Officer to Update",
                "email": "officer-to-update@example.com",
                "phone": "254712345607",
                "role": "Property Officer / Agent",
                "branch_ids": [self.nairobi.pk],
            },
            content_type="application/json",
        )
        self.assertEqual(invited.status_code, 201)
        staff_data = invited.json()["staff"]

        updated = self.client.patch(
            f"/api/v1/director/staff/{staff_data['membership_id']}",
            data={
                "role": "Land Sales Agent",
                "branch_ids": [self.nakuru.pk],
                "active": False,
            },
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["staff"]["roles"], ["Land Sales Agent"])
        self.assertEqual(
            [branch["id"] for branch in updated.json()["staff"]["branches"]],
            [self.nakuru.pk],
        )
        self.assertFalse(updated.json()["staff"]["active"])
        self.assertFalse(
            StaffActivationChallenge.objects.filter(
                membership_id=staff_data["membership_id"]
            ).exists()
        )

        restored = self.client.patch(
            f"/api/v1/director/staff/{staff_data['membership_id']}",
            data={
                "role": "Land Sales Agent",
                "branch_ids": [self.nakuru.pk],
                "active": True,
            },
            content_type="application/json",
        )
        self.assertEqual(restored.status_code, 200)
        updated_membership = AgencyMembership.objects.get(pk=staff_data["membership_id"])
        updated_membership.roles.add(Group.objects.get(name="Branch Manager"))
        resent = self.client.post(
            f"/api/v1/director/staff/{staff_data['membership_id']}/resend-activation"
        )
        self.assertEqual(resent.status_code, 200)
        self.assertTrue(resent.json()["activation_email_sent"])
        self.assertEqual(send_email.call_count, 2)

        challenge = StaffActivationChallenge.objects.get(
            membership_id=staff_data["membership_id"]
        )
        _, challenge_id, code = send_email.call_args.args
        self.assertEqual(str(challenge.pk), str(challenge_id))
        activated = self.client.post(
            "/api/v1/auth/staff/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": code,
                "password": "A-valid-staff-password-53!",
            },
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200)

    @patch("users.views._send_staff_activation_email")
    def test_suspending_staff_access_does_not_disable_another_agency_membership(self, send_email):
        director = self.create_user(email="multi-agency-director@example.com")
        director_membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        director_membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        self.client.force_login(director)
        invited = self.client.post(
            "/api/v1/director/staff",
            data={
                "agency_id": self.nairobi.agency_id,
                "name": "Shared Staff",
                "email": "shared-staff@example.com",
                "phone": "254712345608",
                "role": "Accountant",
                "branch_ids": [self.nairobi.pk],
            },
            content_type="application/json",
        )
        self.assertEqual(invited.status_code, 201)
        staff_user = self.user_model.objects.get(email="shared-staff@example.com")
        challenge = StaffActivationChallenge.objects.get(
            membership__profile=staff_user.profile
        )
        _, _, code = send_email.call_args.args
        activated = self.client.post(
            "/api/v1/auth/staff/activate",
            data={
                "challenge_id": str(challenge.pk),
                "code": code,
                "password": "A-valid-staff-password-52!",
            },
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200)

        other_agency = Agency.objects.create(name="Second Staff Agency", slug="second-staff-agency")
        other_branch = Branch.objects.create(
            agency=other_agency,
            name="Second Branch",
            town="Nakuru",
        )
        other_membership = AgencyMembership.objects.create(
            profile=staff_user.profile,
            agency=other_agency,
        )
        other_membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        other_membership.branches.add(other_branch)
        invited_membership = AgencyMembership.objects.get(
            pk=invited.json()["staff"]["membership_id"]
        )
        invited_membership.roles.add(Group.objects.get(name="Branch Manager"))

        suspended = self.client.delete(
            f"/api/v1/director/staff/{invited.json()['staff']['membership_id']}"
        )
        self.assertEqual(suspended.status_code, 200)
        staff_user.refresh_from_db()
        other_membership.refresh_from_db()
        self.assertTrue(staff_user.is_active)
        self.assertTrue(other_membership.is_active)
        self.client.logout()
        staff_login = self.client.post(
            "/api/v1/auth/login",
            data={
                "identifier": staff_user.email,
                "password": "A-valid-staff-password-52!",
            },
            content_type="application/json",
        )
        self.assertEqual(staff_login.status_code, 200)
        self.assertIn(
            "Property Officer / Agent",
            staff_login.json()["user"]["roles"],
        )

    def test_seeded_srs_roles_and_branches_exist(self):
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)),
            {
                "Super Admin (Director)",
                "Branch Manager",
                "Accountant",
                "Property Officer / Agent",
                "Land Sales Agent",
                "Caretaker",
                "Landlord (portal)",
                "Tenant (portal)",
                "Land Buyer (portal)",
            },
        )
        self.assertEqual(
            set(Branch.objects.values_list("name", flat=True)),
            {"Nairobi", "Nakuru"},
        )

    def test_user_can_have_multiple_roles_and_branches(self):
        user = self.create_user()
        user.groups.add(
            Group.objects.get(name="Accountant"),
            Group.objects.get(name="Caretaker"),
        )
        user.profile.branches.add(self.nairobi, self.nakuru)

        self.assertEqual(user.groups.count(), 2)
        self.assertEqual(
            set(user.profile.accessible_branches()),
            {self.nairobi, self.nakuru},
        )

    def test_super_admin_can_access_all_branches(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        user.profile.branches.add(self.nairobi)

        self.assertEqual(
            set(user.profile.accessible_branches()),
            {self.nairobi, self.nakuru},
        )
        self.assertTrue(user.has_perm("auth.change_group"))
        self.assertTrue(user.has_perm("users.change_branch"))
        self.assertTrue(user.has_perm("users.view_auditlog"))
        self.assertFalse(user.has_perm("users.add_auditlog"))

    def test_branch_endpoints_require_authentication(self):
        self.assertEqual(self.client.get("/api/v1/branches").status_code, 401)
        self.assertEqual(
            self.client.get(f"/api/v1/branches/{self.nairobi.pk}").status_code,
            401,
        )

    def test_branch_endpoints_enforce_server_side_scope(self):
        user = self.create_user()
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)

        response = self.client.get("/api/v1/branches")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["branches"],
            [{"id": self.nairobi.pk, "name": "Nairobi", "town": "Nairobi"}],
        )
        self.assertEqual(
            self.client.get(f"/api/v1/branches/{self.nairobi.pk}").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/branches/{self.nakuru.pk}").status_code,
            404,
        )

    def test_landlord_api_requires_staff_role(self):
        user = self.create_user()
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)

        response = self.client.get("/api/v1/landlords")

        self.assertEqual(response.status_code, 403)

    def test_landlord_api_is_branch_scoped_and_audited(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)

        created = self.client.post(
            "/api/v1/landlords/create",
            data={
                "branch_id": self.nairobi.pk,
                "name": "Nairobi Estates Ltd",
                "id_or_registration_number": "C.123456",
                "kra_pin": "P051234567A",
                "phone": "254712345679",
                "email": "owner@example.com",
                "payout_details": {"bank_name": "Example Bank", "account_name": "Nairobi Estates"},
            },
            content_type="application/json",
        )

        self.assertEqual(created.status_code, 201)
        landlord_id = created.json()["landlord"]["id"]
        event = AuditLog.objects.get(
            action="landlord.created",
            entity="Landlord",
        )
        self.assertEqual(event.user, user)
        self.assertEqual(event.new_values["branch_id"], self.nairobi.pk)
        self.assertEqual(event.new_values["kra_pin"], "[redacted]")
        self.assertEqual(
            event.new_values["payout_details"],
            {"bank_name": "[redacted]", "account_name": "[redacted]"},
        )

        visible = self.client.get("/api/v1/landlords")
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(len(visible.json()["landlords"]), 1)

        hidden = Landlord.objects.create(
            branch=self.nakuru,
            name="Nakuru Estates",
            id_or_registration_number="C.654321",
            phone="254712345680",
        )
        self.assertEqual(
            self.client.get(f"/api/v1/landlords/{hidden.pk}").status_code,
            404,
        )

        agreement = self.client.post(
            f"/api/v1/landlords/{landlord_id}/agreements",
            data={
                "effective_from": "2026-01-01",
                "commission_model": "percentage",
                "commission_rate": "8.50",
                "payout_day": 5,
                "mri_enabled": True,
                "terms": {"notice_days": 30},
            },
            content_type="application/json",
        )

        self.assertEqual(agreement.status_code, 201)
        saved_agreement = ManagementAgreement.objects.get(landlord_id=landlord_id)
        self.assertEqual(saved_agreement.commission_rate, Decimal("8.50"))
        self.assertTrue(
            AuditLog.objects.filter(
                action="management_agreement.created",
                entity="ManagementAgreement",
                user=user,
            ).exists()
        )

    def test_landlord_api_rejects_invalid_agreement_dates(self):
        landlord = Landlord.objects.create(
            branch=self.nairobi,
            name="Invalid Date Owner",
            id_or_registration_number="ID-INVALID-DATE",
            phone="254712345679",
        )
        user = self.create_user(phone="254712345680")
        user.groups.add(Group.objects.get(name="Accountant"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)

        response = self.client.post(
            f"/api/v1/landlords/{landlord.pk}/agreements",
            data={
                "effective_from": "2026-06-01",
                "effective_to": "2026-05-01",
                "commission_model": "fixed",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ManagementAgreement.objects.exists())

    def test_property_api_is_branch_scoped_and_assigns_caretakers(self):
        user = self.create_user(phone="254712345681")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        caretaker = self.create_user(
            email="caretaker@example.com",
            phone="254712345682",
            name="Nairobi Caretaker",
        )
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        self.client.force_login(user)

        response = self.client.post(
            "/api/v1/properties/create",
            data={
                "branch_id": self.nairobi.pk,
                "name": "Nairobi Heights",
                "property_type": "Apartment",
                "location": "Westlands",
                "caretaker_ids": [caretaker.pk],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        property_obj = Property.objects.get(name="Nairobi Heights")
        self.assertEqual(list(property_obj.caretakers.values_list("pk", flat=True)), [caretaker.pk])
        self.assertEqual(self.client.get("/api/v1/properties").json()["properties"][0]["name"], "Nairobi Heights")
        self.assertTrue(
            AuditLog.objects.filter(action="property.created", entity="Property", user=user).exists()
        )

    def test_property_registration_rejects_units_before_separate_import(self):
        user = self.create_user(
            email="bulk-property-officer@example.com",
            phone="254712345683",
        )
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_data = {
            "branch_id": self.nairobi.pk,
            "name": "Bulk Setup Apartments",
            "property_type": "Apartment",
            "location": "Kilimani",
            "units": [
                {
                    "code": "BSA-01",
                    "unit_type": "One bedroom",
                    "bedrooms": 1,
                    "status": Unit.STATUS_VACANT,
                    "rent": "28000.00",
                    "rent_effective_from": "2026-11-01",
                },
                {
                    "code": "BSA-02",
                    "unit_type": "Two bedroom",
                    "bedrooms": 2,
                    "status": Unit.STATUS_VACANT,
                    "rent": "35000.00",
                    "rent_effective_from": "2026-11-01",
                },
            ],
        }

        created = self.client.post(
            "/api/v1/properties/create",
            data=json.dumps(property_data),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 400, created.content)
        self.assertEqual(created.json()["title"], "Unit import required")
        self.assertFalse(Property.objects.filter(name="Bulk Setup Apartments").exists())
        self.assertFalse(Unit.objects.filter(code__in=("BSA-01", "BSA-02")).exists())

    def test_staff_property_and_unit_lists_follow_branch_access(self):
        nairobi_property = Property.objects.create(
            branch=self.nairobi,
            name="Nairobi List Property",
            property_type="Apartment",
            location="Nairobi",
        )
        nakuru_property = Property.objects.create(
            branch=self.nakuru,
            name="Nakuru List Property",
            property_type="Apartment",
            location="Nakuru",
        )
        nairobi_unit = Unit.objects.create(
            property=nairobi_property,
            code="LIST-NBO-1",
            unit_type="Studio",
        )
        nakuru_unit = Unit.objects.create(
            property=nakuru_property,
            code="LIST-NKR-1",
            unit_type="Studio",
        )

        for role_index, role_name in enumerate(
            ("Branch Manager", "Accountant", "Property Officer / Agent")
        ):
            staff_user = self.create_user(
                email=f"nairobi-list-{role_index}@example.com",
                phone=f"25471234564{role_index}",
            )
            staff_user.groups.add(Group.objects.get(name=role_name))
            staff_user.profile.branches.add(self.nairobi)
            self.client.force_login(staff_user)

            properties = self.client.get("/api/v1/properties")
            units = self.client.get("/api/v1/units")
            self.assertEqual(properties.status_code, 200, role_name)
            self.assertEqual(units.status_code, 200, role_name)
            self.assertEqual(
                {item["id"] for item in properties.json()["properties"]},
                {nairobi_property.pk},
                role_name,
            )
            self.assertEqual(
                {item["id"] for item in units.json()["units"]},
                {nairobi_unit.pk},
                role_name,
            )
            self.assertNotIn(
                nakuru_property.pk,
                {item["id"] for item in properties.json()["properties"]},
                role_name,
            )
            self.assertNotIn(
                nakuru_unit.pk,
                {item["id"] for item in units.json()["units"]},
                role_name,
            )

        multi_branch_staff = self.create_user(
            email="multi-branch-list-staff@example.com",
            phone="254712345633",
        )
        multi_branch_staff.groups.add(
            Group.objects.get(name="Property Officer / Agent")
        )
        multi_branch_staff.profile.branches.add(self.nairobi, self.nakuru)
        self.client.force_login(multi_branch_staff)
        self.assertEqual(
            {
                item["id"]
                for item in self.client.get("/api/v1/properties").json()["properties"]
            },
            {nairobi_property.pk, nakuru_property.pk},
        )

        director = self.create_user(
            email="director-list-staff@example.com",
            phone="254712345634",
        )
        director.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        director.profile.branches.add(self.nairobi)
        self.client.force_login(director)
        self.assertEqual(
            {
                item["id"]
                for item in self.client.get("/api/v1/properties").json()["properties"]
            },
            {nairobi_property.pk, nakuru_property.pk},
        )
        self.assertEqual(
            {
                item["id"]
                for item in self.client.get("/api/v1/units").json()["units"]
            },
            {nairobi_unit.pk, nakuru_unit.pk},
        )

    def test_property_api_rejects_cross_branch_landlord_and_invalid_caretaker(self):
        user = self.create_user(phone="254712345683")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        landlord = Landlord.objects.create(
            branch=self.nakuru,
            name="Nakuru Owner",
            id_or_registration_number="ID-NAKURU",
            phone="254712345684",
        )
        self.client.force_login(user)

        response = self.client.post(
            "/api/v1/properties/create",
            data={
                "branch_id": self.nairobi.pk,
                "name": "Invalid Assignment",
                "property_type": "Apartment",
                "location": "Nairobi",
                "landlord_id": landlord.pk,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Property.objects.filter(name="Invalid Assignment").exists())

    def test_landlord_portal_and_staff_access_enforce_landlord_branch_scope(self):
        landlord_user, first_property = self.create_portal_landlord(
            email="cross-branch-landlord@example.com"
        )
        multi_branch_staff = self.create_user(
            email="cross-branch-property-staff@example.com",
            phone="254712345685",
        )
        multi_branch_staff.groups.add(
            Group.objects.get(name="Property Officer / Agent")
        )
        multi_branch_staff.profile.branches.add(self.nairobi, self.nakuru)
        self.client.force_login(multi_branch_staff)

        created = self.client.post(
            "/api/v1/properties/create",
            data={
                "branch_id": self.nakuru.pk,
                "landlord_id": first_property.landlord_id,
                "name": "Cross-Branch Portfolio Property",
                "property_type": "Apartment",
                "location": "Nakuru",
            },
            content_type="application/json",
        )

        self.assertEqual(created.status_code, 400, created.json())
        self.assertFalse(Property.objects.filter(name="Cross-Branch Portfolio Property").exists())

        nakuru_staff = self.create_user(
            email="nakuru-landlord-staff@example.com",
            phone="254712345686",
        )
        nakuru_staff.groups.add(Group.objects.get(name="Property Officer / Agent"))
        nakuru_staff.profile.branches.add(self.nakuru)
        self.client.force_login(nakuru_staff)
        visible_landlords = self.client.get("/api/v1/landlords")
        visible_properties = self.client.get("/api/v1/properties")
        self.assertEqual(visible_landlords.status_code, 200)
        self.assertEqual(visible_landlords.json()["landlords"], [])
        self.assertEqual(visible_properties.json()["properties"], [])

        self.client.force_login(landlord_user)
        properties = self.client.get("/api/v1/landlord/properties")
        units = self.client.get("/api/v1/landlord/units")
        self.assertEqual(properties.status_code, 200)
        self.assertEqual(units.status_code, 200)
        self.assertEqual(
            {item["id"] for item in properties.json()["properties"]},
            {first_property.pk},
        )
        self.assertEqual(units.json()["units"], [])

    def test_property_blocks_floors_and_document_metadata_are_scoped(self):
        user = self.create_user(phone="254712345685")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Documented Property",
            property_type="Commercial",
            location="CBD",
        )

        block_response = self.client.post(
            f"/api/v1/properties/{property_obj.pk}/blocks",
            data={"name": "Block A"},
            content_type="application/json",
        )
        self.assertEqual(block_response.status_code, 201)
        block = Block.objects.get(property=property_obj)
        floor_response = self.client.post(
            f"/api/v1/blocks/{block.pk}/floors",
            data={"name": "Ground Floor", "level": 0},
            content_type="application/json",
        )
        self.assertEqual(floor_response.status_code, 201)
        document_response = self.client.post(
            f"/api/v1/properties/{property_obj.pk}/documents",
            data={
                "document_type": "title_deed",
                "name": "Title deed",
                "storage_key": "properties/1/title-deed.pdf",
                "metadata": {"content_type": "application/pdf"},
            },
            content_type="application/json",
        )
        self.assertEqual(document_response.status_code, 201)
        self.assertTrue(Floor.objects.filter(block=block, level=0).exists())
        self.assertTrue(PropertyDocument.objects.filter(property=property_obj).exists())

    def test_caretaker_account_assignment_and_scope(self):
        manager = self.create_user(email="manager@example.com", phone="254712345686")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        self.client.force_login(manager)

        created = self.client.post(
            "/api/v1/caretakers/create",
            data={
                "email": "caretaker@example.com",
                "name": "Assigned Caretaker",
                "phone": "254712345687",
                "password": "Long-test-passphrase-29!",
                "branch_ids": [self.nairobi.pk],
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        caretaker_id = created.json()["caretaker"]["id"]
        caretaker = self.user_model.objects.get(pk=caretaker_id)
        self.assertTrue(caretaker.groups.filter(name="Caretaker").exists())

        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Caretaker Property",
            property_type="Apartment",
            location="Kilimani",
        )
        assigned = self.client.post(
            f"/api/v1/properties/{property_obj.pk}/caretakers/assign",
            data={"caretaker_id": caretaker_id},
            content_type="application/json",
        )
        self.assertEqual(assigned.status_code, 201)
        self.assertTrue(
            PropertyCaretakerAssignment.objects.filter(
                property=property_obj, caretaker=caretaker, ended_at__isnull=True
            ).exists()
        )

        self.client.force_login(caretaker)
        properties = self.client.get("/api/v1/properties")
        self.assertEqual(properties.status_code, 200)
        self.assertEqual([item["id"] for item in properties.json()["properties"]], [property_obj.pk])
        self.assertEqual(
            self.client.post(
                "/api/v1/caretakers/create",
                data={
                    "email": "unauthorized@example.com",
                    "name": "Unauthorized",
                    "password": "Long-test-passphrase-29!",
                    "branch_ids": [self.nairobi.pk],
                },
                content_type="application/json",
            ).status_code,
            403,
        )

        self.client.force_login(manager)
        ended = self.client.post(
            f"/api/v1/properties/{property_obj.pk}/caretakers/{caretaker_id}/end"
        )
        self.assertEqual(ended.status_code, 200)
        self.assertFalse(
            PropertyCaretakerAssignment.objects.filter(
                property=property_obj, caretaker=caretaker, ended_at__isnull=True
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="caretaker.assigned",
                entity="PropertyCaretakerAssignment",
                user=manager,
            ).exists()
        )

    def test_caretaker_dashboard_and_meter_reading_drafts_are_scoped(self):
        manager = self.create_user(email="dashboard-manager@example.com", phone="254712345688")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="dashboard-caretaker@example.com", phone="254712345689")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Dashboard Property",
            property_type="Apartment",
            location="Kilimani",
        )
        occupied = Unit.objects.create(
            property=property_obj,
            code="D-001",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
            meters={"water": "WM-D001"},
        )
        second_occupied = Unit.objects.create(
            property=property_obj,
            code="D-002",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
            meters={"water": "WM-D002"},
        )
        vacant = Unit.objects.create(
            property=property_obj,
            code="D-003",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
        )
        period_start = (timezone.localdate().replace(day=28) + timedelta(days=4)).replace(day=1)
        for index, unit in enumerate((occupied, second_occupied), start=1):
            tenant = Tenant.objects.create(
                branch=self.nairobi,
                tenant_type=Tenant.INDIVIDUAL,
                name=f"Dashboard Tenant {index}",
                national_id_or_passport=f"ID-DASHBOARD-{index}",
                phone=f"254712340{index:03d}",
            )
            Tenancy.objects.create(
                unit=unit,
                tenant=tenant,
                tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
                start_date=timezone.localdate(),
                base_water_meter_reading=Decimal("10.0000"),
                due_day=5,
            )
            UtilityConfiguration.objects.create(
                unit=unit,
                utility_type=UtilityConfiguration.UTILITY_WATER,
                name="Metered water",
                billing_method=UtilityConfiguration.METHOD_METERED,
                rate_per_unit=Decimal("12.0000"),
                effective_from=period_start,
                metadata={"meter": f"WM-D00{index}"},
            )
            MeterReading.objects.create(
                unit=unit,
                meter=f"WM-D00{index}",
                period=period_start - timedelta(days=1),
                previous_value=Decimal("10.0000"),
                current_value=Decimal(str(10 + index)),
                submitted_by=caretaker,
            )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        self.client.force_login(caretaker)
        today = timezone.localdate()
        pre_due_date = today.replace(day=min(15, calendar.monthrange(today.year, today.month)[1]))
        due_date = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        with patch("users.views.timezone.localdate", return_value=pre_due_date):
            dashboard = self.client.get("/api/v1/caretaker/dashboard")
            water_readings = self.client.get("/api/v1/caretaker/water-readings")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json()["summary"]["properties"], 1)
        self.assertEqual(dashboard.json()["summary"]["occupied"], 2)
        self.assertEqual(dashboard.json()["summary"]["vacant"], 1)
        self.assertFalse(dashboard.json()["readings_due"])
        self.assertEqual(dashboard.json()["summary"]["readings_pending"], 0)
        roster_unit = next(item for item in dashboard.json()["units"] if item["id"] == occupied.pk)
        self.assertEqual(roster_unit["water_meter"], "WM-D001")
        self.assertEqual(roster_unit["water_meter_reading"], "11.0000")
        self.assertEqual(water_readings.status_code, 200, water_readings.content)
        self.assertFalse(water_readings.json()["readings_due"])
        self.assertEqual(water_readings.json()["properties"][0]["pending_count"], 0)
        self.assertEqual(len(water_readings.json()["properties"][0]["units"]), 2)
        with patch("users.views.timezone.localdate", return_value=due_date):
            due_dashboard = self.client.get("/api/v1/caretaker/dashboard")
        self.assertTrue(due_dashboard.json()["readings_due"])
        self.assertEqual(due_dashboard.json()["summary"]["readings_pending"], 2)

        created = self.client.post(
            "/api/v1/caretaker/water-readings/drafts",
            data={
                "property_id": property_obj.pk,
                "period": period_start.isoformat(),
                "readings": [
                    {
                        "unit_id": occupied.pk,
                        "current_value": "14.5",
                    },
                    {
                        "unit_id": second_occupied.pk,
                        "current_value": "21.25",
                    },
                ]
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(created.json()["saved"], 2)
        self.assertEqual(
            MeterReadingDraft.objects.filter(
                unit__in=(occupied, second_occupied),
                period=period_start,
                submitted_by=caretaker,
            ).count(),
            2,
        )
        outside_metered_scope = self.client.post(
            "/api/v1/caretaker/water-readings/drafts",
            data={
                "property_id": property_obj.pk,
                "period": period_start.isoformat(),
                "readings": [{"unit_id": vacant.pk, "current_value": "1"}],
            },
            content_type="application/json",
        )
        self.assertEqual(outside_metered_scope.status_code, 400, outside_metered_scope.content)

        self.client.force_login(manager)
        self.assertEqual(self.client.get("/api/v1/caretaker/dashboard").status_code, 403)

    def test_caretaker_dashboard_shows_paid_water_and_assigned_property_viewings(self):
        manager = self.create_user(
            email="site-hub-manager@example.com",
            phone="254712345640",
        )
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(
            email="site-hub-caretaker@example.com",
            phone="254712345641",
        )
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Site Hub Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="SH-001",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        next_period = (timezone.localdate().replace(day=28) + timedelta(days=4)).replace(day=1)
        UtilityConfiguration.objects.create(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Fixed water charge",
            billing_method=UtilityConfiguration.METHOD_FIXED,
            fixed_amount=Decimal("75.00"),
            effective_from=next_period,
        )
        lead = ViewingLead.objects.create(
            branch=self.nairobi,
            property=property_obj,
            unit=unit,
            full_name="Site Hub Prospect",
            phone="254712345699",
            created_by=manager,
        )
        PropertyViewing.objects.create(
            lead=lead,
            scheduled_for=timezone.now(),
            scheduled_by=manager,
            assigned_to=caretaker,
        )
        self.client.force_login(caretaker)

        response = self.client.get("/api/v1/caretaker/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["properties"][0]["has_paid_water"])
        self.assertFalse(response.json()["properties"][0]["has_metered_water"])
        self.assertEqual(
            [viewing["prospect_name"] for viewing in response.json()["viewings"]],
            ["Site Hub Prospect"],
        )

    def test_caretaker_can_complete_only_assigned_move_in_checklist_items(self):
        manager = self.create_user(email="checklist-manager@example.com", phone="254712345642")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="checklist-caretaker@example.com", phone="254712345643")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Checklist Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="CL-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Checklist Tenant",
            national_id_or_passport="ID-CHECKLIST-001",
            phone="254712345644",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_FIXED_TERM,
            start_date=timezone.localdate(),
            end_date=date(2099, 12, 31),
            due_day=5,
        )
        package = MoveInPackage.objects.create(
            tenancy=tenancy,
            agreement_reference="MOVE-IN-CHECKLIST-001",
        )
        item = MoveInChecklistItem.objects.create(package=package, title="Record key handover")
        unassigned_property = Property.objects.create(
            branch=self.nairobi,
            name="Unassigned Checklist Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unassigned_unit = Unit.objects.create(
            property=unassigned_property,
            code="CL-002",
            unit_type="Studio",
        )
        unassigned_tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Unassigned Checklist Tenant",
            national_id_or_passport="ID-CHECKLIST-002",
            phone="254712345645",
        )
        unassigned_tenancy = Tenancy.objects.create(
            unit=unassigned_unit,
            tenant=unassigned_tenant,
            tenancy_type=Tenancy.TYPE_FIXED_TERM,
            start_date=timezone.localdate(),
            end_date=date(2099, 12, 31),
            due_day=5,
        )
        unassigned_package = MoveInPackage.objects.create(
            tenancy=unassigned_tenancy,
            agreement_reference="MOVE-IN-CHECKLIST-002",
        )
        unassigned_item = MoveInChecklistItem.objects.create(
            package=unassigned_package,
            title="Record second key handover",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        self.client.force_login(caretaker)

        dashboard = self.client.get("/api/v1/caretaker/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.content)
        checklist = dashboard.json()["checklists"][0]
        self.assertEqual(checklist["property_name"], property_obj.name)
        self.assertEqual(checklist["unit_code"], unit.code)

        completed = self.client.patch(
            f"/api/v1/caretaker/checklists/{item.pk}",
            data={"status": "completed", "notes": "Two keys handed over."},
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        item.refresh_from_db()
        self.assertEqual(item.status, MoveInChecklistItem.STATUS_COMPLETED)
        self.assertEqual(item.notes, "Two keys handed over.")
        self.assertIsNotNone(item.completed_at)
        outside_scope = self.client.patch(
            f"/api/v1/caretaker/checklists/{unassigned_item.pk}",
            data={"status": "completed", "notes": ""},
            content_type="application/json",
        )
        self.assertEqual(outside_scope.status_code, 404, outside_scope.content)

    def test_move_in_handover_records_evidence_and_requires_property_officer_confirmation(self):
        manager = self.create_user(email="handover-manager@example.com", phone="254712345646")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="handover-caretaker@example.com", phone="254712345647")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Handover Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="HO-001",
            unit_type="One bedroom",
            bedrooms=1,
            meters={"water": "W-001"},
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Handover Tenant",
            national_id_or_passport="ID-HANDOVER-001",
            phone="254712345648",
        )
        start_date = timezone.localdate()
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_FIXED_TERM,
            status=Tenancy.STATUS_ACTIVE,
            start_date=start_date,
            end_date=date(2099, 12, 31),
            due_day=5,
        )
        UtilityConfiguration.objects.create(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("10.0000"),
            effective_from=start_date,
            metadata={"meter": "W-001"},
        )
        package = MoveInPackage.objects.create(
            tenancy=tenancy,
            agreement_reference="MOVE-IN-HANDOVER-001",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        self.client.force_login(caretaker)

        pending = self.client.get("/api/v1/caretaker/checklists")
        self.assertEqual(pending.status_code, 200, pending.content)
        handover = pending.json()["handovers"][0]
        self.assertEqual(handover["id"], package.pk)
        self.assertEqual(handover["meter_requirements"][0]["meter"], "W-001")

        photo = SimpleUploadedFile(
            "condition.png",
            b"\x89PNG\r\n\x1a\ncondition",
            content_type="image/png",
        )
        with patch("users.views.upload_private_object") as store_photo:
            uploaded = self.client.post(
                f"/api/v1/caretaker/checklists/{package.pk}/photos",
                {"photos": photo},
            )
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        store_photo.assert_called_once()

        conditions = [
            {
                "room": room,
                "area": area,
                "status": "needs_attention" if room == "Living room" and area == "Walls" else "good",
                "notes": (
                    "Paint needs repair"
                    if room == "Living room" and area == "Walls"
                    else ""
                ),
            }
            for room in ("Living room", "Kitchen", "Bathroom", "Bedroom 1")
            for area in (
                "Walls",
                "Doors",
                "Locks",
                "Windows",
                "Plumbing fixtures",
                "Electrical sockets",
            )
        ]
        submitted = self.client.post(
            f"/api/v1/caretaker/checklists/{package.pk}/submit",
            data=json.dumps(
                {
                    "keys_issued": 2,
                    "condition_checklist": conditions,
                    "initial_meter_readings": [
                        {"utility_type": "water", "current_value": "18.25"}
                    ],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(submitted.status_code, 200, submitted.content)
        package.refresh_from_db()
        tenancy.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(package.handover_status, MoveInPackage.HANDOVER_STATUS_SUBMITTED)
        self.assertEqual(package.keys_issued, 2)
        self.assertIsNone(tenancy.base_water_meter_reading)
        self.assertNotIn("water_reading", unit.meters)
        self.assertEqual(
            SMSDelivery.objects.filter(template_name="move_in_handover_submitted").count(),
            1,
        )
        self.assertEqual(
            set(
                MoveInChecklistItem.objects.filter(
                    package=package,
                    title__in=MoveInChecklistItem.CARETAKER_HANDOVER_ITEM_TITLES,
                    status=MoveInChecklistItem.STATUS_COMPLETED,
                ).values_list("title", flat=True)
            ),
            set(MoveInChecklistItem.CARETAKER_HANDOVER_ITEM_TITLES),
        )

        self.client.force_login(manager)
        review_queue = self.client.get("/api/v1/property-officer/move-in-handovers")
        self.assertEqual(review_queue.status_code, 200, review_queue.content)
        self.assertEqual(len(review_queue.json()["handovers"]), 1)
        with patch("users.views.read_private_object", return_value=b"private-photo") as read_photo:
            photo_response = self.client.get(
                f"/api/v1/property-officer/move-in-handovers/{package.pk}/photos/0"
            )
        self.assertEqual(photo_response.status_code, 200, photo_response.content)
        self.assertEqual(photo_response.content, b"private-photo")
        read_photo.assert_called_once()
        confirmed = self.client.post(
            f"/api/v1/property-officer/move-in-handovers/{package.pk}/confirm",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        package.refresh_from_db()
        tenancy.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(package.handover_status, MoveInPackage.HANDOVER_STATUS_CONFIRMED)
        self.assertEqual(tenancy.base_water_meter_reading, Decimal("18.25"))
        self.assertEqual(unit.meters["water_reading"], "18.25")
        self.assertEqual(
            SMSDelivery.objects.filter(template_name="move_in_handover_confirmed").count(),
            2,
        )

    def test_move_in_handover_rejects_missing_opening_meter_reading(self):
        manager = self.create_user(email="missing-reading-manager@example.com", phone="254712345649")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="missing-reading-caretaker@example.com", phone="254712345650")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Missing Reading Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="MR-001",
            unit_type="Studio",
            meters={"water": "W-002"},
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Missing Reading Tenant",
            national_id_or_passport="ID-MISSING-READING-001",
            phone="254712345651",
        )
        start_date = timezone.localdate()
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_FIXED_TERM,
            status=Tenancy.STATUS_ACTIVE,
            start_date=start_date,
            end_date=date(2099, 12, 31),
            due_day=5,
        )
        UtilityConfiguration.objects.create(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("10.0000"),
            effective_from=start_date,
            metadata={"meter": "W-002"},
        )
        package = MoveInPackage.objects.create(
            tenancy=tenancy,
            agreement_reference="MOVE-IN-HANDOVER-002",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        self.client.force_login(caretaker)
        response = self.client.post(
            f"/api/v1/caretaker/checklists/{package.pk}/submit",
            data=json.dumps(
                {
                    "keys_issued": 1,
                    "condition_checklist": [
                        {"room": room, "area": area, "status": "good", "notes": ""}
                        for room in ("Studio", "Kitchen", "Bathroom")
                        for area in (
                            "Walls",
                            "Doors",
                            "Locks",
                            "Windows",
                            "Plumbing fixtures",
                            "Electrical sockets",
                        )
                    ],
                    "initial_meter_readings": [],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        package.refresh_from_db()
        self.assertEqual(package.handover_status, MoveInPackage.HANDOVER_STATUS_PENDING)

    def test_maintenance_ticket_lifecycle_is_scoped_and_audited(self):
        manager = self.create_user(email="ticket-manager@example.com", phone="254712345690")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="ticket-caretaker@example.com", phone="254712345691")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Maintenance Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="MT-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Ticket Tenant",
            national_id_or_passport="ID-TICKET-001",
            phone="254712345693",
        )
        Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_FIXED_TERM,
            start_date=timezone.localdate(),
            end_date=date(2099, 12, 31),
            due_day=5,
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        self.client.force_login(manager)
        vendor_response = self.client.post(
            "/api/v1/vendors/create",
            data={
                "branch_id": self.nairobi.pk,
                "name": "Kilimani Plumbing Services",
                "trade": "Plumbing",
                "phone": "254712345692",
                "mpesa_number": "254712345692",
                "rating": "4.50",
            },
            content_type="application/json",
        )
        self.assertEqual(vendor_response.status_code, 201)
        vendor_id = vendor_response.json()["vendor"]["id"]
        self.client.force_login(caretaker)
        created = self.client.post(
            "/api/v1/maintenance-tickets/create",
            data={
                "property_id": property_obj.pk,
                "unit_id": unit.pk,
                "title": "Leaking kitchen tap",
                "description": "Water is leaking continuously.",
                "category": "plumbing",
                "priority": "high",
                "vendor_id": vendor_id,
                "photos": [{"storage_key": "tickets/tap.jpg", "content_type": "image/jpeg"}],
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        ticket_id = created.json()["ticket"]["id"]
        self.assertEqual(created.json()["ticket"]["vendor"]["id"], vendor_id)
        self.assertEqual(created.json()["ticket"]["status"], MaintenanceTicket.STATUS_OPEN)

        assigned = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/status",
            data={"status": "assigned"},
            content_type="application/json",
        )
        self.assertEqual(assigned.status_code, 200)
        ticket = MaintenanceTicket.objects.get(pk=ticket_id)
        self.assertEqual(ticket.assigned_to_id, caretaker.pk)
        progressing = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/status",
            data={"status": "in_progress"},
            content_type="application/json",
        )
        self.assertEqual(progressing.status_code, 200)
        comment = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/comments",
            data={
                "body": "Fundi has started the repair.",
                "photos": [{"storage_key": "tickets/work.jpg"}],
            },
            content_type="application/json",
        )
        self.assertEqual(comment.status_code, 201)
        self.assertEqual(MaintenanceTicketComment.objects.filter(ticket_id=ticket_id).count(), 1)
        incomplete = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/status",
            data={"status": "completed"},
            content_type="application/json",
        )
        self.assertEqual(incomplete.status_code, 400)
        self.assertEqual(
            MaintenanceTicket.objects.get(pk=ticket_id).status,
            MaintenanceTicket.STATUS_IN_PROGRESS,
        )
        missing_completion_photo = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/status",
            data={
                "status": "completed",
                "fundi_name": "Kilimani Repairs",
                "repair_cost": "1250.50",
            },
            content_type="application/json",
        )
        self.assertEqual(missing_completion_photo.status_code, 409)
        negative_cost = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/status",
            data={
                "status": "completed",
                "fundi_name": "Kilimani Repairs",
                "repair_cost": "-1",
            },
            content_type="application/json",
        )
        self.assertEqual(negative_cost.status_code, 400)
        with patch("users.views.upload_private_object") as store_photo:
            evidence = self.client.post(
                f"/api/v1/maintenance-tickets/{ticket_id}/completion-photos",
                data={
                    "photos": SimpleUploadedFile(
                        "repair-complete.jpg",
                        b"\xff\xd8\xffcompletion evidence",
                        content_type="image/jpeg",
                    ),
                },
            )
        self.assertEqual(evidence.status_code, 201, evidence.content)
        store_photo.assert_called_once()
        self.assertEqual(
            MaintenanceTicket.objects.get(pk=ticket_id).photos[-1]["evidence_type"],
            "completion",
        )
        completed = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/status",
            data={
                "status": "completed",
                "fundi_name": "Kilimani Repairs",
                "repair_cost": "1250.50",
            },
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200)
        completed_ticket = MaintenanceTicket.objects.get(pk=ticket_id)
        self.assertEqual(completed_ticket.resolution_fundi_name, "Kilimani Repairs")
        self.assertEqual(completed_ticket.repair_cost, Decimal("1250.50"))
        self.assertEqual(
            SMSDelivery.objects.filter(
                template_name="maintenance_ticket_completed_staff"
            ).count(),
            1,
        )
        self.assertEqual(
            SMSDelivery.objects.filter(
                template_name="maintenance_ticket_completed_tenant"
            ).count(),
            1,
        )
        common_area_ticket = self.client.post(
            "/api/v1/maintenance-tickets/create",
            data={
                "property_id": property_obj.pk,
                "title": "Lobby light out",
                "description": "The lobby light does not turn on.",
                "category": "electrical",
                "priority": "normal",
            },
            content_type="application/json",
        )
        self.assertEqual(common_area_ticket.status_code, 201, common_area_ticket.content)
        self.assertIsNone(common_area_ticket.json()["ticket"]["unit_id"])

        self.client.force_login(manager)
        listed = self.client.get("/api/v1/maintenance-tickets?status=in_progress")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["tickets"]), 0)
        completed_tickets = self.client.get("/api/v1/maintenance-tickets?status=completed")
        self.assertEqual(len(completed_tickets.json()["tickets"]), 1)
        month = completed_ticket.completed_at.strftime("%Y-%m")
        ledger = self.client.get(f"/api/v1/maintenance-ledger?month={month}")
        self.assertEqual(ledger.status_code, 200)
        self.assertEqual(ledger.json()["summary"]["total_spend"], "1250.50")
        self.assertEqual(ledger.json()["summary"]["total_repairs"], 1)
        self.assertEqual(ledger.json()["ledger"][0]["fundi_name"], "Kilimani Repairs")
        self.assertEqual(ledger.json()["ledger"][0]["cost"], "1250.50")
        self.assertIn(
            month,
            self.client.get("/api/v1/maintenance-ledger/months").json()["months"],
        )
        self.assertEqual(
            self.client.get("/api/v1/maintenance-ledger?month=2026-13").status_code,
            400,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="maintenance_ticket.status_changed",
                entity="MaintenanceTicket",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action="vendor.created", entity="Vendor").exists()
        )

    def test_tenant_maintenance_is_scoped_audited_and_supports_private_evidence(self):
        portal_user, tenant, tenancy = self.create_tenant_portal("81")
        other_user, _, other_tenancy = self.create_tenant_portal("82")
        other_ticket = MaintenanceTicket.objects.create(
            property=other_tenancy.unit.property,
            unit=other_tenancy.unit,
            reported_by=other_user,
            title="Other tenant issue",
            description="This must remain private.",
            category=MaintenanceTicket.CATEGORY_OTHER,
        )
        self.client.force_login(portal_user)
        evidence = SimpleUploadedFile(
            "leak.png",
            b"\x89PNG\r\n\x1a\n" + b"image-data",
            content_type="image/png",
        )
        with patch("users.views.upload_private_object") as upload:
            created = self.client.post(
                "/api/v1/tenant/maintenance-tickets/create",
                data={
                    "category": "plumbing",
                    "description": "Water is leaking under the kitchen sink.",
                    "priority": "high",
                    "property_id": other_tenancy.unit.property_id,
                    "unit_id": other_tenancy.unit_id,
                    "evidence": evidence,
                },
            )
        self.assertEqual(created.status_code, 201)
        ticket = MaintenanceTicket.objects.get(pk=created.json()["ticket"]["id"])
        self.assertEqual(ticket.property_id, tenancy.unit.property_id)
        self.assertEqual(ticket.unit_id, tenancy.unit_id)
        self.assertEqual(ticket.reported_by_id, portal_user.pk)
        self.assertEqual(ticket.status, MaintenanceTicket.STATUS_OPEN)
        self.assertEqual(ticket.photos[0]["content_type"], "image/png")
        self.assertTrue(ticket.photos[0]["storage_key"].startswith(f"maintenance/tenant/{tenant.pk}/"))
        upload.assert_called_once()
        property_manager = self.create_user(
            email="tenant-maintenance-manager@example.com",
            phone="254712345687",
        )
        property_manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        property_manager.profile.branches.add(self.nairobi)
        MaintenanceTicketComment.objects.create(
            ticket=ticket,
            author=property_manager,
            body="The property team has received your request.",
        )

        listed = self.client.get("/api/v1/tenant/maintenance-tickets")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [row["id"] for row in listed.json()["tickets"]],
            [ticket.pk],
        )
        comments_url = f"/api/v1/tenant/maintenance-tickets/{ticket.pk}/comments"
        added = self.client.post(
            comments_url,
            data={"body": "The leak has become worse."},
            content_type="application/json",
        )
        self.assertEqual(added.status_code, 201)
        self.assertEqual(added.json()["comment"]["sender_role"], "tenant")
        visible_comments = self.client.get(comments_url).json()["comments"]
        self.assertEqual(
            [comment["message"] for comment in visible_comments],
            [
                "The property team has received your request.",
                "The leak has become worse.",
            ],
        )
        self.assertEqual(visible_comments[0]["sender_role"], "staff")
        self.assertEqual(
            self.client.get(
                f"/api/v1/tenant/maintenance-tickets/{other_ticket.pk}/comments"
            ).status_code,
            404,
        )
        with patch("users.views.read_private_object", return_value=b"private-image") as read:
            evidence_response = self.client.get(
                f"/api/v1/tenant/maintenance-tickets/{ticket.pk}/evidence/0"
            )
        self.assertEqual(evidence_response.status_code, 200)
        self.assertEqual(evidence_response.content, b"private-image")
        self.assertEqual(evidence_response["Cache-Control"], "private, no-store")
        read.assert_called_once_with(ticket.photos[0]["storage_key"])
        self.assertEqual(
            self.client.get(
                f"/api/v1/tenant/maintenance-tickets/{other_ticket.pk}/evidence/0"
            ).status_code,
            404,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=portal_user,
                action="maintenance_ticket.tenant_created",
                entity="MaintenanceTicket",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=portal_user,
                action="maintenance_ticket.tenant_comment_added",
                entity="MaintenanceTicketComment",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=portal_user,
                action="maintenance_ticket.evidence_viewed",
                entity="MaintenanceTicket",
            ).exists()
        )

    def test_tenant_maintenance_requires_linked_tenant_and_active_tenancy_to_create(self):
        self.assertEqual(
            self.client.get("/api/v1/tenant/maintenance-tickets").status_code,
            401,
        )
        non_tenant = self.create_user(
            email="not-tenant-maintenance@example.com",
            phone="254712345683",
        )
        self.client.force_login(non_tenant)
        self.assertEqual(
            self.client.get("/api/v1/tenant/maintenance-tickets").status_code,
            403,
        )
        unlinked_user = self.create_user(
            email="unlinked-tenant-maintenance@example.com",
            phone="254712345684",
        )
        unlinked_user.groups.add(Group.objects.get(name="Tenant (portal)"))
        self.client.force_login(unlinked_user)
        self.assertEqual(
            self.client.get("/api/v1/tenant/maintenance-tickets").status_code,
            404,
        )
        inactive_user, _, _ = self.create_tenant_portal("86", active=False)
        self.client.force_login(inactive_user)
        response = self.client.post(
            "/api/v1/tenant/maintenance-tickets/create",
            data={
                "category": "plumbing",
                "description": "There is a leak.",
                "priority": "normal",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["title"], "Active tenancy required")
        self.assertEqual(MaintenanceTicket.objects.count(), 0)

    def test_tenant_maintenance_rejects_bad_evidence_and_reports_storage_failures(self):
        portal_user, _, _ = self.create_tenant_portal("85")
        self.client.force_login(portal_user)
        invalid_image = SimpleUploadedFile(
            "not-an-image.jpg",
            b"not an image",
            content_type="image/jpeg",
        )
        with patch("users.views.upload_private_object") as upload:
            invalid = self.client.post(
                "/api/v1/tenant/maintenance-tickets/create",
                data={
                    "category": "plumbing",
                    "description": "There is a leak.",
                    "priority": "normal",
                    "evidence": invalid_image,
                },
            )
        self.assertEqual(invalid.status_code, 400)
        upload.assert_not_called()

        valid_image = SimpleUploadedFile(
            "leak.png",
            b"\x89PNG\r\n\x1a\n" + b"image-data",
            content_type="image/png",
        )
        with (
            patch("users.views.upload_private_object", side_effect=ImproperlyConfigured),
            patch("users.views.delete_private_object") as delete,
        ):
            unavailable = self.client.post(
                "/api/v1/tenant/maintenance-tickets/create",
                data={
                    "category": "plumbing",
                    "description": "There is a leak.",
                    "priority": "normal",
                    "evidence": valid_image,
                },
            )
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(MaintenanceTicket.objects.count(), 0)
        delete.assert_called_once()

    def test_landlord_portal_sees_and_replies_to_owned_property_maintenance(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="maintenance-landlord-portal@example.com"
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="OWNER-MT-1",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant_user = self.create_user(
            email="maintenance-tenant-owner@example.com",
            phone="254712345688",
        )
        tenant_user.groups.add(Group.objects.get(name="Tenant (portal)"))
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Maintenance Board Tenant",
            national_id_or_passport="MT-BOARD-TENANT",
            phone="254712345688",
            email=tenant_user.email,
            portal_user=tenant_user,
        )
        Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=timezone.localdate() - timedelta(days=10),
        )
        ticket = MaintenanceTicket.objects.create(
            property=property_obj,
            unit=unit,
            reported_by=tenant_user,
            title="Leaking tap",
            description="The kitchen tap is leaking.",
            category=MaintenanceTicket.CATEGORY_PLUMBING,
            status=MaintenanceTicket.STATUS_COMPLETED,
            completed_at=timezone.now(),
            resolution_fundi_name="Owner's plumber",
            repair_cost=Decimal("800.00"),
            photos=[
                {
                    "storage_key": f"maintenance/tenant/{tenant.pk}/leak.png",
                    "content_type": "image/png",
                    "size_bytes": 100,
                    "name": "Evidence 1.png",
                }
            ],
        )
        unrelated_property = Property.objects.create(
            branch=self.nairobi,
            name="Unrelated Maintenance Property",
            property_type="Apartment",
            location="Nairobi",
        )
        unrelated_ticket = MaintenanceTicket.objects.create(
            property=unrelated_property,
            reported_by=tenant_user,
            title="Unrelated issue",
            description="This property is not owned by the landlord.",
            category=MaintenanceTicket.CATEGORY_OTHER,
            status=MaintenanceTicket.STATUS_COMPLETED,
            completed_at=timezone.now(),
            resolution_fundi_name="Other contractor",
            repair_cost=Decimal("3000.00"),
        )
        self.client.force_login(landlord_user)
        listed = self.client.get("/api/v1/maintenance-tickets")
        self.assertEqual(listed.status_code, 200)
        ticket_row = next(
            row for row in listed.json()["tickets"] if row["id"] == ticket.pk
        )
        self.assertEqual(ticket_row["reported_by_name"], tenant_user.profile.name)
        self.assertFalse(ticket_row["can_update"])
        self.assertEqual(len(ticket_row["evidence"]), 1)
        self.assertNotIn(
            unrelated_ticket.pk,
            {row["id"] for row in listed.json()["tickets"]},
        )
        status_update = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket.pk}/status",
            data={"status": "assigned"},
            content_type="application/json",
        )
        self.assertEqual(status_update.status_code, 403)
        comments_url = f"/api/v1/maintenance-tickets/{ticket.pk}/comments"
        reply = self.client.post(
            comments_url,
            data={"body": "A caretaker will inspect the tap."},
            content_type="application/json",
        )
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.json()["comment"]["sender_role"], "landlord")
        self.assertEqual(len(self.client.get(comments_url).json()["comments"]), 1)
        ledger_month = ticket.completed_at.strftime("%Y-%m")
        ledger = self.client.get(f"/api/v1/maintenance-ledger?month={ledger_month}")
        self.assertEqual(ledger.status_code, 200)
        self.assertEqual(ledger.json()["summary"]["total_spend"], "800.00")
        self.assertEqual(
            [row["id"] for row in ledger.json()["ledger"]],
            [ticket.pk],
        )
        with patch("users.views.read_private_object", return_value=b"landlord-visible-image"):
            evidence = self.client.get(
                f"/api/v1/maintenance-tickets/{ticket.pk}/evidence/0"
            )
        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(evidence.content, b"landlord-visible-image")
        self.assertEqual(
            self.client.get(
                f"/api/v1/maintenance-tickets/{unrelated_ticket.pk}/evidence/0"
            ).status_code,
            404,
        )

    def test_move_out_notice_settlement_approval_and_payout_are_scoped_and_audited(self):
        manager = self.create_user(email="moveout-manager@example.com", phone="254712345610")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        approver = self.create_user(email="moveout-approver@example.com", phone="254712345611")
        approver.groups.add(Group.objects.get(name="Branch Manager"))
        approver.profile.branches.add(self.nairobi)
        finance = self.create_user(email="moveout-finance@example.com", phone="254712345612")
        finance.groups.add(Group.objects.get(name="Accountant"))
        finance.profile.branches.add(self.nairobi)
        tenant_user, tenant, tenancy = self.create_tenant_portal("91")
        today = timezone.localdate()
        move_out_date = today + timedelta(days=tenancy.notice_period_days)
        unit = tenancy.unit
        unit.status = Unit.STATUS_OCCUPIED
        unit.meters = {"water": "WM-MOVEOUT"}
        unit.save(update_fields=["status", "meters", "updated_at"])
        DepositAccount.objects.create(
            tenancy=tenancy,
            target_amount=Decimal("1000.00"),
            balance=Decimal("1000.00"),
        )
        invoice_start = today - timedelta(days=60)
        Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-MOVEOUT-001",
            period_start=invoice_start,
            period_end=invoice_start + timedelta(days=29),
            due_date=today - timedelta(days=30),
            total=Decimal("500.00"),
        )
        UtilityConfiguration.objects.create(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("20.0000"),
            minimum_charge=Decimal("0.00"),
            effective_from=tenancy.start_date,
            metadata={"meter": "WM-MOVEOUT"},
        )
        MeterReading.objects.create(
            unit=unit,
            meter="WM-MOVEOUT",
            period=today - timedelta(days=1),
            previous_value=Decimal("100.0000"),
            current_value=Decimal("110.0000"),
            submitted_by=manager,
        )
        inspection = PropertyInspection.objects.create(
            property=unit.property,
            unit=unit,
            title="Move-out inspection",
            inspection_type="move_out",
            scheduled_for=timezone.now(),
            responsible_staff=manager,
            created_by=manager,
            status=PropertyInspection.STATUS_COMPLETED,
            checklist=[{"title": "Keys returned", "status": "complete"}],
            photos=[{
                "storage_key": "inspections/move-out/91/exit.jpg",
                "content_type": "image/jpeg",
                "name": "Exit inspection evidence 1.jpg",
            }],
            findings="Window damage documented.",
            completed_at=timezone.now(),
        )

        self.client.force_login(tenant_user)
        notice = self.client.post(
            "/api/v1/tenant/move-out-notice/create",
            data={
                "proposed_move_out_date": move_out_date.isoformat(),
                "reason": "Moving for work.",
            },
            content_type="application/json",
        )
        self.assertEqual(notice.status_code, 201)
        notice_id = notice.json()["notice"]["id"]
        self.assertEqual(
            self.client.get("/api/v1/tenant/move-out-notice").json()["notice"]["id"],
            notice_id,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/tenant/move-out-notices/999999/withdraw"
            ).status_code,
            404,
        )
        self.client.force_login(manager)
        accepted = self.client.post(
            f"/api/v1/move-out-notices/{notice_id}/decision",
            data={"action": "accept"},
            content_type="application/json",
        )
        self.assertEqual(accepted.status_code, 200)
        tenancy.refresh_from_db()
        self.assertEqual(tenancy.end_date, move_out_date)
        self.assertEqual(unit.__class__.objects.get(pk=unit.pk).status, Unit.STATUS_NOTICE_GIVEN)

        workflows = self.client.get("/api/v1/move-out-workflows")
        self.assertEqual(workflows.status_code, 200)
        self.assertEqual(workflows.json()["eligible_tenancies"][0]["deposit_balance"], "1000.00")
        with patch("users.views.timezone.localdate", return_value=move_out_date):
            submitted = self.client.post(
                "/api/v1/move-out-settlements/create",
                data={
                    "tenancy_id": tenancy.pk,
                    "notice_id": notice_id,
                    "exit_inspection_id": inspection.pk,
                    "actual_move_out_date": move_out_date.isoformat(),
                    "final_readings": [
                        {"utility_type": "water", "current_value": "120.0000"}
                    ],
                    "damage_items": [{"description": "Broken window", "amount": "600.00"}],
                },
                content_type="application/json",
            )
        self.assertEqual(submitted.status_code, 201, submitted.content)
        settlement = submitted.json()["settlement"]
        settlement_id = settlement["id"]
        self.assertEqual(settlement["deposit_balance"], "1000.00")
        self.assertEqual(settlement["arrears_amount"], "500.00")
        self.assertEqual(settlement["utility_amount"], "200.00")
        self.assertEqual(settlement["damages_amount"], "600.00")
        self.assertEqual(settlement["deduction_from_deposit"], "1000.00")
        self.assertEqual(settlement["refund_amount"], "0.00")
        self.assertEqual(settlement["shortfall_amount"], "300.00")
        self.assertEqual(tenancy.__class__.objects.get(pk=tenancy.pk).status, Tenancy.STATUS_ACTIVE)
        self.assertEqual(Unit.objects.get(pk=unit.pk).status, Unit.STATUS_DEPOSIT_PENDING)
        self.client.force_login(tenant_user)
        tenant_tracker = self.client.get("/api/v1/tenant/dashboard")
        self.assertEqual(tenant_tracker.status_code, 200)
        tracker_entry = tenant_tracker.json()["move_out_settlements"][0]
        self.assertEqual(tracker_entry["shortfall_amount"], "300.00")
        self.assertEqual(tracker_entry["inspection_photos"][0]["caption"], "Exit inspection evidence 1.jpg")
        self.assertNotIn("storage_key", tracker_entry["inspection_photos"][0])
        with patch("users.views.read_private_object", return_value=b"test-image"):
            photo = self.client.get(
                f"/api/v1/tenant/move-out-settlements/{settlement_id}/photos/0"
            )
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo["Content-Type"], "image/jpeg")

        self.client.force_login(approver)
        decision_url = f"/api/v1/move-out-settlements/{settlement_id}/decision"
        self.assertEqual(
            self.client.post(
                decision_url,
                data={"action": "reject"},
                content_type="application/json",
            ).status_code,
            400,
        )
        rejected = self.client.post(
            decision_url,
            data={"action": "reject", "review_note": "Please verify the damage amount."},
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["settlement"]["status"], MoveOutSettlement.STATUS_REJECTED)
        self.client.force_login(manager)
        with patch("users.views.timezone.localdate", return_value=move_out_date):
            resubmitted = self.client.post(
                "/api/v1/move-out-settlements/create",
                data={
                    "tenancy_id": tenancy.pk,
                    "notice_id": notice_id,
                    "exit_inspection_id": inspection.pk,
                    "actual_move_out_date": move_out_date.isoformat(),
                    "final_readings": [
                        {"utility_type": "water", "current_value": "120.0000"}
                    ],
                    "damage_items": [{"description": "Broken window", "amount": "600.00"}],
                },
                content_type="application/json",
            )
        self.assertEqual(resubmitted.status_code, 201, resubmitted.content)
        self.assertEqual(resubmitted.json()["settlement"]["id"], settlement_id)
        self.assertEqual(
            self.client.post(
                decision_url,
                data={"action": "approve"},
                content_type="application/json",
            ).status_code,
            403,
        )
        self.client.force_login(approver)
        approved = self.client.post(
            f"/api/v1/move-out-settlements/{settlement_id}/decision",
            data={"action": "approve"},
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200)
        self.client.force_login(finance)
        with patch("users.views.timezone.localdate", return_value=move_out_date):
            paid = self.client.post(
                f"/api/v1/move-out-settlements/{settlement_id}/payout",
                data={"payout_date": move_out_date.isoformat()},
                content_type="application/json",
            )
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(paid.json()["settlement"]["status"], MoveOutSettlement.STATUS_PAID)
        self.assertEqual(DepositAccount.objects.get(tenancy=tenancy).balance, Decimal("0.00"))
        self.assertEqual(
            DepositEntry.objects.filter(
                account__tenancy=tenancy,
                entry_type=DepositEntry.TYPE_DEDUCTION,
            ).values_list("amount", flat=True).get(),
            Decimal("1000.00"),
        )
        self.assertEqual(Unit.objects.get(pk=unit.pk).status, Unit.STATUS_VACANT)
        self.assertEqual(
            tenancy.__class__.objects.get(pk=tenancy.pk).status,
            Tenancy.STATUS_ENDED,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="move_out_settlement.paid",
                entity="MoveOutSettlement",
            ).exists()
        )
        for template in (
            "move_out_notice_submitted",
            "move_out_notice_updated",
            "move_out_settlement_submitted",
            "move_out_settlement_approved",
            "move_out_settlement_paid",
        ):
            self.assertTrue(
                SMSDelivery.objects.filter(template_name=template).exists(),
                f"Expected a queued SMS for {template}.",
            )

    def test_move_out_notice_and_settlement_require_scope_and_valid_inputs(self):
        tenant_user, _, tenancy = self.create_tenant_portal("92")
        self.client.force_login(tenant_user)
        too_soon = self.client.post(
            "/api/v1/tenant/move-out-notice/create",
            data={
                "proposed_move_out_date": (
                    timezone.localdate() + timedelta(days=1)
                ).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(too_soon.status_code, 400)
        self.client.force_login(
            self.create_user(email="moveout-unrelated@example.com", phone="254712345613")
        )
        self.assertEqual(self.client.get("/api/v1/move-out-workflows").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/v1/move-out-settlements/create",
                data={"tenancy_id": tenancy.pk},
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_caretaker_exit_inspection_uploads_evidence_and_closes_to_maintenance(self):
        manager = self.create_user(email="moveout-inspection-manager@example.com", phone="254712345620")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="moveout-inspection-caretaker@example.com", phone="254712345621")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        approver = self.create_user(email="moveout-inspection-approver@example.com", phone="254712345622")
        approver.groups.add(Group.objects.get(name="Branch Manager"))
        approver.profile.branches.add(self.nairobi)
        finance = self.create_user(email="moveout-inspection-finance@example.com", phone="254712345623")
        finance.groups.add(Group.objects.get(name="Accountant"))
        finance.profile.branches.add(self.nairobi)
        tenant_user, tenant, tenancy = self.create_tenant_portal("96")
        PropertyCaretakerAssignment.objects.create(
            property=tenancy.unit.property,
            caretaker=caretaker,
            assigned_by=manager,
        )
        today = timezone.localdate()
        proposed_date = today + timedelta(days=tenancy.notice_period_days)

        self.client.force_login(tenant_user)
        notice_response = self.client.post(
            "/api/v1/tenant/move-out-notice/create",
            data={"proposed_move_out_date": proposed_date.isoformat()},
            content_type="application/json",
        )
        self.assertEqual(notice_response.status_code, 201)
        self.assertEqual(
            Unit.objects.get(pk=tenancy.unit_id).status,
            Unit.STATUS_NOTICE_GIVEN,
        )
        self.client.force_login(manager)
        accepted = self.client.post(
            f"/api/v1/move-out-notices/{notice_response.json()['notice']['id']}/decision",
            data={"action": "accept"},
            content_type="application/json",
        )
        self.assertEqual(accepted.status_code, 200)
        self.client.force_login(caretaker)
        caretaker_scheduled = self.client.post(
            "/api/v1/inspections/create",
            data={
                "property_id": tenancy.unit.property_id,
                "unit_id": tenancy.unit_id,
                "title": "Exit inspection",
                "inspection_type": "move_out",
                "scheduled_for": timezone.now().isoformat(),
                "responsible_staff_id": caretaker.pk,
            },
            content_type="application/json",
        )
        self.assertEqual(caretaker_scheduled.status_code, 403)
        self.client.force_login(manager)
        created_inspection = self.client.post(
            "/api/v1/inspections/create",
            data={
                "property_id": tenancy.unit.property_id,
                "unit_id": tenancy.unit_id,
                "title": "Exit inspection",
                "inspection_type": "move_out",
                "scheduled_for": timezone.now().isoformat(),
                "responsible_staff_id": caretaker.pk,
            },
            content_type="application/json",
        )
        self.assertEqual(created_inspection.status_code, 201, created_inspection.content)
        inspection = created_inspection.json()["inspection"]
        self.client.force_login(caretaker)
        listed = self.client.get("/api/v1/inspections")
        self.assertEqual(
            [item["id"] for item in listed.json()["inspections"]],
            [inspection["id"]],
        )
        image = SimpleUploadedFile(
            "exit.jpg",
            b"\xff\xd8\xffmove-out-evidence",
            content_type="image/jpeg",
        )
        with patch("users.views.upload_private_object"):
            uploaded = self.client.post(
                f"/api/v1/inspections/{inspection['id']}/photos",
                data={"photos": image},
            )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        completed = self.client.post(
            f"/api/v1/inspections/{inspection['id']}/complete",
            data={
                "actual_move_out_date": today.isoformat(),
                "checklist": [
                    {"title": item["title"], "status": "complete"}
                    for item in inspection["checklist"]
                ],
                "findings": "A damaged door was documented.",
                "damage_items": [{"description": "Replace door", "amount": "500"}],
                "maintenance_required": True,
            },
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        self.assertEqual(len(completed.json()["inspection"]["photos"]), 1)

        self.client.force_login(manager)
        settlement_response = self.client.post(
            "/api/v1/move-out-settlements/create",
            data={
                "tenancy_id": tenancy.pk,
                "notice_id": notice_response.json()["notice"]["id"],
                "exit_inspection_id": inspection["id"],
                "actual_move_out_date": today.isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(settlement_response.status_code, 201, settlement_response.content)
        settlement_id = settlement_response.json()["settlement"]["id"]
        self.assertEqual(settlement_response.json()["settlement"]["damages_amount"], "500.00")
        self.assertTrue(settlement_response.json()["settlement"]["maintenance_required"])
        self.client.force_login(approver)
        self.assertEqual(
            self.client.post(
                f"/api/v1/move-out-settlements/{settlement_id}/decision",
                data={"action": "approve"},
                content_type="application/json",
            ).status_code,
            200,
        )
        self.client.force_login(finance)
        payout = self.client.post(
            f"/api/v1/move-out-settlements/{settlement_id}/payout",
            data={"payout_date": today.isoformat()},
            content_type="application/json",
        )
        self.assertEqual(payout.status_code, 200, payout.content)
        self.assertEqual(Unit.objects.get(pk=tenancy.unit_id).status, Unit.STATUS_MAINTENANCE)
        self.assertEqual(
            Tenancy.objects.get(pk=tenancy.pk).status,
            Tenancy.STATUS_ENDED,
        )

    def test_move_out_notice_rejection_can_be_resubmitted_and_withdrawn(self):
        manager = self.create_user(email="moveout-review@example.com", phone="254712345614")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        tenant_user, _, tenancy = self.create_tenant_portal("93")
        proposed_date = timezone.localdate() + timedelta(days=tenancy.notice_period_days)

        self.client.force_login(tenant_user)
        first_notice = self.client.post(
            "/api/v1/tenant/move-out-notice/create",
            data={"proposed_move_out_date": proposed_date.isoformat()},
            content_type="application/json",
        )
        self.assertEqual(first_notice.status_code, 201)
        notice_id = first_notice.json()["notice"]["id"]

        self.client.force_login(manager)
        rejected = self.client.post(
            f"/api/v1/move-out-notices/{notice_id}/decision",
            data={"action": "reject", "review_note": "Please confirm your date."},
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["notice"]["status"], MoveOutNotice.STATUS_REJECTED)

        self.client.force_login(tenant_user)
        second_notice = self.client.post(
            "/api/v1/tenant/move-out-notice/create",
            data={"proposed_move_out_date": proposed_date.isoformat()},
            content_type="application/json",
        )
        self.assertEqual(second_notice.status_code, 201)

        self.client.force_login(manager)
        accepted = self.client.post(
            f"/api/v1/move-out-notices/{second_notice.json()['notice']['id']}/decision",
            data={"action": "accept"},
            content_type="application/json",
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(tenancy.__class__.objects.get(pk=tenancy.pk).end_date, proposed_date)
        withdrawn = self.client.post(
            f"/api/v1/move-out-notices/{second_notice.json()['notice']['id']}/decision",
            data={"action": "withdraw"},
            content_type="application/json",
        )
        self.assertEqual(withdrawn.status_code, 200)
        tenancy.refresh_from_db()
        self.assertIsNone(tenancy.end_date)
        self.assertEqual(Unit.objects.get(pk=tenancy.unit_id).status, Unit.STATUS_OCCUPIED)

    def test_quotation_approval_threshold_and_cross_branch_landlord_cost_allocation(self):
        manager = self.create_user(email="quote-manager@example.com", phone="254712345693")
        manager.groups.add(Group.objects.get(name="Branch Manager"))
        manager.profile.branches.add(self.nakuru)
        caretaker = self.create_user(email="quote-caretaker@example.com", phone="254712345694")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nakuru)
        landlord = Landlord.objects.create(
            branch=self.nairobi,
            name="Quote Landlord",
            id_or_registration_number="QL-001",
            phone="254712345695",
        )
        ManagementAgreement.objects.create(
            landlord=landlord,
            effective_from=date(2026, 1, 1),
            commission_model="percentage",
            commission_rate=Decimal("10.00"),
            terms={"maintenance_approval_threshold": "1000.00"},
        )
        property_obj = Property.objects.create(
            branch=self.nakuru,
            landlord=landlord,
            name="Quote Property",
            property_type="Apartment",
            location="Kilimani",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        ticket = MaintenanceTicket.objects.create(
            property=property_obj,
            reported_by=caretaker,
            assigned_to=caretaker,
            title="Broken socket",
            description="Socket is unsafe.",
            category=MaintenanceTicket.CATEGORY_ELECTRICAL,
            status=MaintenanceTicket.STATUS_ASSIGNED,
        )
        vendor = Vendor.objects.create(
            branch=self.nakuru,
            name="Electrical Fundi",
            trade="Electrical",
            phone="254712345696",
        )
        self.client.force_login(caretaker)
        quote = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket.pk}/quotations/create",
            data={
                "vendor_id": vendor.pk,
                "description": "Replace socket and wiring",
                "amount": "2500.00",
                "valid_until": "2026-12-01",
            },
            content_type="application/json",
        )
        self.assertEqual(quote.status_code, 201)
        quotation_id = quote.json()["quotation"]["id"]
        self.assertEqual(MaintenanceQuotation.objects.get(pk=quotation_id).status, "submitted")
        ticket.refresh_from_db()
        self.assertEqual(
            ticket.status,
            MaintenanceTicket.STATUS_AWAITING_LANDLORD_APPROVAL,
        )

        self.client.force_login(manager)
        approved = self.client.post(
            f"/api/v1/maintenance-quotations/{quotation_id}/approval",
            data={"approved": True},
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["quotation"]["status"], "approved")
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id, caretaker.pk)
        allocation = self.client.post(
            f"/api/v1/maintenance-quotations/{quotation_id}/allocations/create",
            data={"target": "landlord", "amount": "2500.00", "reason": "Owner responsibility"},
            content_type="application/json",
        )
        self.assertEqual(allocation.status_code, 201)
        self.assertEqual(
            MaintenanceCostAllocation.objects.get(quotation_id=quotation_id).landlord_id,
            landlord.pk,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="maintenance_quotation.approved",
                entity="MaintenanceQuotation",
            ).exists()
        )

    def test_landlord_quotation_review_is_owner_scoped_and_threshold_limited(self):
        manager = self.create_user(
            email="landlord-quote-manager@example.com",
            phone="254712345686",
        )
        landlord_user, property_obj = self.create_portal_landlord(
            email="landlord-quote-owner@example.com"
        )
        _other_landlord_user, other_property = self.create_portal_landlord(
            email="landlord-quote-other@example.com"
        )
        today = timezone.localdate()
        for landlord in (property_obj.landlord, other_property.landlord):
            ManagementAgreement.objects.create(
                landlord=landlord,
                effective_from=today,
                commission_model="percentage",
                commission_rate=Decimal("10.00"),
                terms={"maintenance_approval_threshold": "1000.00"},
            )
        vendor = Vendor.objects.create(
            branch=self.nairobi,
            name="Landlord Review Repairs",
            trade="Plumbing",
            phone="254712345687",
        )

        def create_quote(property_record, suffix, amount):
            ticket = MaintenanceTicket.objects.create(
                property=property_record,
                reported_by=manager,
                assigned_to=manager,
                title=f"Repair {suffix}",
                description=f"Maintenance work {suffix}.",
                category=MaintenanceTicket.CATEGORY_PLUMBING,
                status=MaintenanceTicket.STATUS_AWAITING_LANDLORD_APPROVAL,
            )
            return MaintenanceQuotation.objects.create(
                ticket=ticket,
                vendor=vendor,
                description=f"Quotation for {suffix}",
                amount=Decimal(amount),
                status=MaintenanceQuotation.STATUS_SUBMITTED,
                submitted_by=manager,
            )

        own_high_quote = create_quote(property_obj, "high", "2500.00")
        own_second_high_quote = create_quote(property_obj, "second-high", "3000.00")
        own_low_quote = create_quote(property_obj, "low", "500.00")
        other_high_quote = create_quote(other_property, "other-high", "4000.00")

        self.client.force_login(landlord_user)
        pending = self.client.get("/api/v1/landlord/maintenance/quotations")
        self.assertEqual(pending.status_code, 200, pending.content)
        self.assertEqual(
            {item["id"] for item in pending.json()["quotations"]},
            {own_high_quote.pk, own_second_high_quote.pk},
        )
        self.assertEqual(
            pending.json()["quotations"][0]["approval_threshold"],
            "1000.00",
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/landlord/maintenance/quotations/{own_low_quote.pk}/decision",
                data={"approved": True},
                content_type="application/json",
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/landlord/maintenance/quotations/{other_high_quote.pk}/decision",
                data={"approved": True},
                content_type="application/json",
            ).status_code,
            404,
        )
        blank_rejection = self.client.post(
            f"/api/v1/landlord/maintenance/quotations/{own_second_high_quote.pk}/decision",
            data={"approved": False, "rejection_reason": "  "},
            content_type="application/json",
        )
        self.assertEqual(blank_rejection.status_code, 400)
        rejected = self.client.post(
            f"/api/v1/landlord/maintenance/quotations/{own_second_high_quote.pk}/decision",
            data={
                "approved": False,
                "rejection_reason": "The scope needs another estimate.",
            },
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 200, rejected.content)
        self.assertEqual(
            rejected.json()["quotation"]["status"],
            MaintenanceQuotation.STATUS_REJECTED,
        )
        self.assertEqual(
            MaintenanceQuotation.objects.get(pk=own_second_high_quote.pk).rejection_reason,
            "The scope needs another estimate.",
        )
        own_second_high_quote.ticket.refresh_from_db()
        self.assertEqual(
            own_second_high_quote.ticket.status,
            MaintenanceTicket.STATUS_QUOTATION,
        )
        approved = self.client.post(
            f"/api/v1/landlord/maintenance/quotations/{own_high_quote.pk}/decision",
            data={"approved": True},
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(
            approved.json()["quotation"]["status"],
            MaintenanceQuotation.STATUS_APPROVED,
        )
        own_high_quote.ticket.refresh_from_db()
        self.assertEqual(own_high_quote.ticket.status, MaintenanceTicket.STATUS_IN_PROGRESS)
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="maintenance_quotation.approved",
                entity="MaintenanceQuotation",
            ).exists()
        )

    def test_scheduled_inspection_completion_and_shared_report(self):
        manager = self.create_user(email="inspection-manager@example.com", phone="254712345697")
        manager.groups.add(Group.objects.get(name="Branch Manager"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="inspection-caretaker@example.com", phone="254712345698")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Inspection Property",
            property_type="Apartment",
            location="Westlands",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        self.client.force_login(caretaker)
        created = self.client.post(
            "/api/v1/inspections/create",
            data={
                "property_id": property_obj.pk,
                "title": "Quarterly property inspection",
                "inspection_type": "routine",
                "scheduled_for": "2026-11-15T09:00:00+03:00",
                "responsible_staff_id": caretaker.pk,
                "checklist": [{"title": "Check common areas", "status": "pending"}],
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        inspection_id = created.json()["inspection"]["id"]
        completed = self.client.post(
            f"/api/v1/inspections/{inspection_id}/complete",
            data={
                "checklist": [{"title": "Check common areas", "status": "completed", "notes": "Clean"}],
                "photos": [{"storage_key": "inspections/1/common-area.jpg", "caption": "Common area"}],
                "findings": "No urgent defects.",
                "recommendations": "Continue quarterly checks.",
            },
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["inspection"]["status"], PropertyInspection.STATUS_COMPLETED)
        self.client.force_login(manager)
        shared = self.client.post(f"/api/v1/inspections/{inspection_id}/share")
        self.assertEqual(shared.status_code, 200)
        token = shared.json()["report"]["share_token"]
        public_report = self.client.get(f"/api/v1/inspection-reports/{token}")
        self.assertEqual(public_report.status_code, 200)
        self.assertEqual(public_report.json()["report"]["findings"], "No urgent defects.")
        self.assertNotIn("share_token", public_report.json()["report"])
        self.assertTrue(
            AuditLog.objects.filter(
                action="property_inspection.report_shared",
                entity="PropertyInspection",
            ).exists()
        )

    def test_recurring_property_expense_threshold_and_audit(self):
        manager = self.create_user(email="expense-manager@example.com", phone="254712345699")
        manager.groups.add(Group.objects.get(name="Branch Manager"))
        manager.profile.branches.add(self.nairobi)
        caretaker = self.create_user(email="expense-caretaker@example.com", phone="254712345600")
        caretaker.groups.add(Group.objects.get(name="Caretaker"))
        caretaker.profile.branches.add(self.nairobi)
        landlord = Landlord.objects.create(
            branch=self.nairobi,
            name="Expense Landlord",
            id_or_registration_number="EL-001",
            phone="254712345601",
        )
        ManagementAgreement.objects.create(
            landlord=landlord,
            effective_from=date(2026, 1, 1),
            commission_model="percentage",
            terms={"expense_approval_threshold": "5000.00"},
        )
        property_obj = Property.objects.create(
            branch=self.nairobi,
            landlord=landlord,
            name="Expense Property",
            property_type="Apartment",
            location="Kilimani",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=manager,
        )
        self.client.force_login(caretaker)
        created = self.client.post(
            "/api/v1/property-expenses/create",
            data={
                "property_id": property_obj.pk,
                "category": "Security",
                "description": "Night guard service",
                "amount": "4500.00",
                "recurrence": "monthly",
                "effective_from": "2026-11-01",
                "next_due_date": "2026-12-01",
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        expense_id = created.json()["expense"]["id"]
        self.assertEqual(
            PropertyExpense.objects.get(pk=expense_id).status,
            PropertyExpense.STATUS_SUBMITTED,
        )
        approved = self.client.post(
            f"/api/v1/property-expenses/{expense_id}/approval",
            data={"approved": True},
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 403)
        self.client.force_login(manager)
        approved = self.client.post(
            f"/api/v1/property-expenses/{expense_id}/approval",
            data={"approved": True},
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["expense"]["status"], PropertyExpense.STATUS_APPROVED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="property_expense.approved",
                entity="PropertyExpense",
            ).exists()
        )

    def test_announcements_are_published_and_tenant_scoped(self):
        manager = self.create_user(email="announcement-manager@example.com", phone="254712345602")
        manager.groups.add(Group.objects.get(name="Branch Manager"))
        manager.profile.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Notice Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="NOTICE-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Notice Tenant",
            national_id_or_passport="NOTICE-ID",
            phone="254712345603",
            email="notice-tenant@example.com",
        )
        portal_user = self.create_user(email="notice-tenant@example.com", phone="254712345603")
        portal_user.groups.add(Group.objects.get(name="Tenant (portal)"))
        tenant.portal_user = portal_user
        tenant.save(update_fields=["portal_user", "updated_at"])
        Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
        )
        self.client.force_login(manager)
        created = self.client.post(
            "/api/v1/announcements/create",
            data={
                "branch_id": self.nairobi.pk,
                "property_id": property_obj.pk,
                "title": "Water interruption",
                "body": "Water will be interrupted tomorrow.",
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        announcement_id = created.json()["announcement"]["id"]
        published = self.client.post(f"/api/v1/announcements/{announcement_id}/publish")
        self.assertEqual(published.status_code, 200)
        self.client.force_login(portal_user)
        notices = self.client.get("/api/v1/announcements")
        self.assertEqual(notices.status_code, 200)
        self.assertEqual(len(notices.json()["announcements"]), 1)
        self.assertEqual(notices.json()["announcements"][0]["title"], "Water interruption")
        self.assertNotIn("created_by", notices.json()["announcements"][0])
        other = Announcement.objects.create(
            branch=self.nairobi,
            title="Draft notice",
            body="Not visible.",
            status=Announcement.STATUS_DRAFT,
            created_by=manager,
        )
        self.assertEqual(other.status, Announcement.STATUS_DRAFT)
        self.assertTrue(
            AuditLog.objects.filter(
                action="announcement.published",
                entity="Announcement",
            ).exists()
        )

    def test_unit_api_validates_scope_and_status_transitions(self):
        user = self.create_user(phone="254712345686")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Unit Property",
            property_type="Apartment",
            location="Kilimani",
        )
        block = Block.objects.create(property=property_obj, name="Block A")
        floor = Floor.objects.create(block=block, name="Ground", level=0)

        created = self.client.post(
            "/api/v1/units/create",
            data={
                "property_id": property_obj.pk,
                "floor_id": floor.pk,
                "code": "A-001",
                "unit_type": "One bedroom",
                "bedrooms": 1,
                "meters": {"water": "WM-001", "electricity": "EM-001"},
                "listing_enabled": True,
                "status": Unit.STATUS_VACANT,
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        unit = Unit.objects.get(code="A-001")
        self.assertEqual(created.json()["unit"]["status"], Unit.STATUS_VACANT)

        occupied = self.client.post(
            f"/api/v1/units/{unit.pk}/status",
            data={"status": Unit.STATUS_OCCUPIED},
            content_type="application/json",
        )
        self.assertEqual(occupied.status_code, 200)
        unit.refresh_from_db()
        self.assertEqual(unit.status, Unit.STATUS_OCCUPIED)
        self.assertFalse(unit.listing_enabled)
        self.assertTrue(
            AuditLog.objects.filter(
                action="unit.status_changed", entity="Unit", user=user
            ).exists()
        )

        invalid = self.client.post(
            f"/api/v1/units/{unit.pk}/status",
            data={"status": Unit.STATUS_DRAFT},
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        unit.refresh_from_db()
        self.assertEqual(unit.status, Unit.STATUS_OCCUPIED)

    def test_unit_api_rejects_invalid_listing_state_and_cross_branch_unit(self):
        user = self.create_user(phone="254712345687")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Invalid Unit Property",
            property_type="Apartment",
            location="Lavington",
        )

        invalid = self.client.post(
            "/api/v1/units/create",
            data={
                "property_id": property_obj.pk,
                "code": "A-002",
                "unit_type": "Studio",
                "status": Unit.STATUS_OCCUPIED,
                "listing_enabled": True,
            },
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(Unit.objects.filter(code="A-002").exists())

        self.assertEqual(self.client.get("/api/v1/units").status_code, 200)
        other_property = Property.objects.create(
            branch=self.nakuru,
            name="Hidden Unit Property",
            property_type="Apartment",
            location="Nakuru",
        )
        hidden = Unit.objects.create(
            property=other_property,
            code="N-001",
            unit_type="Studio",
        )
        self.assertEqual(
            self.client.get(f"/api/v1/units/{hidden.pk}").status_code,
            404,
        )

    def test_vacancy_board_filters_accessible_units_and_current_rent(self):
        user = self.create_user(phone="254712345689")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Vacancy Board Property",
            property_type="Apartment",
            location="Kilimani",
        )
        listed = Unit.objects.create(
            property=property_obj,
            code="V-001",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
            listing_enabled=True,
        )
        UnitCharge.objects.create(
            unit=listed,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("25000.00"),
            effective_from=date.today(),
        )
        Unit.objects.create(
            property=property_obj,
            code="V-002",
            unit_type="Two bedroom",
            status=Unit.STATUS_OCCUPIED,
            listing_enabled=False,
        )
        other_property = Property.objects.create(
            branch=self.nakuru,
            name="Other Branch Property",
            property_type="Apartment",
            location="Kilimani",
        )
        Unit.objects.create(
            property=other_property,
            code="N-001",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
            listing_enabled=True,
        )

        response = self.client.get(
            "/api/v1/vacancy-board",
            {"area": "kilimani", "unit_type": "studio", "rent_min": "20000", "rent_max": "30000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["vacancies"]], [listed.pk])
        self.assertEqual(response.json()["vacancies"][0]["rent"], "25000.00")
        self.assertEqual(
            self.client.get("/api/v1/vacancy-board", {"branch_id": self.nakuru.pk}).status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/v1/vacancy-board", {"rent_min": "not-a-number"}).status_code,
            400,
        )

    def test_unit_history_returns_preserved_records_and_audit_events(self):
        user = self.create_user(phone="254712345690")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="History Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="H-001",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
            listing_enabled=True,
        )

        charge_response = self.client.post(
            f"/api/v1/units/{unit.pk}/charges/create",
            data={
                "charge_type": "rent",
                "name": "Monthly rent",
                "amount": "30000.00",
                "effective_from": date.today().isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(charge_response.status_code, 201)
        status_response = self.client.post(
            f"/api/v1/units/{unit.pk}/status",
            data={"status": Unit.STATUS_OCCUPIED},
            content_type="application/json",
        )
        self.assertEqual(status_response.status_code, 200)

        response = self.client.get(f"/api/v1/units/{unit.pk}/history")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unit"]["id"], unit.pk)
        self.assertEqual(len(response.json()["charges"]), 1)
        self.assertEqual(response.json()["charges"][0]["amount"], "30000.00")
        self.assertEqual(len(response.json()["status_changes"]), 1)
        self.assertEqual(response.json()["status_changes"][0]["action"], "unit.status_changed")
        self.assertEqual(len(response.json()["charge_history"]), 1)
        self.assertEqual(response.json()["maintenance"], [])

        other_property = Property.objects.create(
            branch=self.nakuru,
            name="Hidden History Property",
            property_type="Apartment",
            location="Nakuru",
        )
        hidden = Unit.objects.create(property=other_property, code="N-002", unit_type="Studio")
        self.assertEqual(
            self.client.get(f"/api/v1/units/{hidden.pk}/history").status_code,
            404,
        )

    def test_tenancy_invoice_is_branch_sequential_idempotent_and_immutable(self):
        user = self.create_user(phone="254712345693")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Invoice Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="I-001",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Invoice Tenant",
            national_id_or_passport="ID-INVOICE-1",
            phone="254712345694",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=31,
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("30000.00"),
            effective_from=date(2026, 1, 1),
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RECURRING,
            name="Service charge",
            amount=Decimal("2500.00"),
            effective_from=date(2026, 1, 1),
        )

        created = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/invoices/create",
            data={"period_start": "2026-02-01", "period_end": "2026-02-28"},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        invoice = Invoice.objects.get(tenancy=tenancy)
        self.assertEqual(created.json()["invoice"]["number"], f"INV-{self.nairobi.pk}-2026-000001")
        self.assertEqual(created.json()["invoice"]["total"], "32500.00")
        self.assertEqual(created.json()["invoice"]["due_date"], "2026-02-28")
        self.assertEqual(len(created.json()["invoice"]["lines"]), 2)

        repeated = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/invoices/create",
            data={"period_start": "2026-02-01", "period_end": "2026-02-28"},
            content_type="application/json",
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["invoice"]["id"], invoice.pk)
        self.assertEqual(Invoice.objects.filter(tenancy=tenancy).count(), 1)

        with self.assertRaises(ValidationError):
            invoice.total = Decimal("1.00")
            invoice.save()
        with self.assertRaises(ValidationError):
            invoice.delete()

    def test_property_officer_billing_holds_missing_water_and_approves_waiver(self):
        officer = self.create_user(
            email="billing-wizard-officer@example.com",
            phone="254712341101",
        )
        officer_membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        officer_membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        officer_membership.branches.add(self.nairobi)
        self.client.force_login(officer)

        today = timezone.localdate()
        period_start = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Billing Wizard Property",
            property_type="Apartment",
            location="Kilimani",
        )
        water_unit = Unit.objects.create(
            property=property_obj,
            code="BW-001",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
            meters={"water": "BW-METER"},
        )
        standard_unit = Unit.objects.create(
            property=property_obj,
            code="BW-002",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        tenancies = []
        for index, unit in enumerate((water_unit, standard_unit), start=1):
            tenant = Tenant.objects.create(
                branch=self.nairobi,
                tenant_type=Tenant.INDIVIDUAL,
                name=f"Billing Wizard Tenant {index}",
                national_id_or_passport=f"ID-BILLING-WIZARD-{index}",
                phone=f"254712340{index:03d}",
            )
            tenancy = Tenancy.objects.create(
                unit=unit,
                tenant=tenant,
                tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
                start_date=period_start,
                due_day=5,
            )
            tenancies.append(tenancy)
            UnitCharge.objects.create(
                unit=unit,
                charge_type=UnitCharge.CHARGE_RENT,
                name="Monthly rent",
                amount=Decimal("1000.00"),
                effective_from=period_start,
            )
        UtilityConfiguration.objects.create(
            unit=water_unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("10.0000"),
            effective_from=period_start,
        )

        month = period_start.strftime("%Y-%m")
        director = self.create_user(
            email="billing-wizard-director@example.com",
            phone="254712341104",
        )
        director_membership, _ = AgencyMembership.objects.get_or_create(
            profile=director.profile,
            agency=self.nairobi.agency,
        )
        director_membership.roles.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        pre_invoice_waiver_response = self.client.post(
            "/api/v1/property-officer/billing/waivers",
            data={
                "property_id": property_obj.pk,
                "unit_id": water_unit.pk,
                "month": month,
                "amount": "100.00",
                "reason": "Temporary water service interruption",
            },
            content_type="application/json",
        )
        self.assertEqual(pre_invoice_waiver_response.status_code, 201, pre_invoice_waiver_response.content)
        pre_invoice_waiver = InvoiceWaiverRequest.objects.get(unit=water_unit, period_start=period_start)
        duplicate_waiver_response = self.client.post(
            "/api/v1/property-officer/billing/waivers",
            data={
                "property_id": property_obj.pk,
                "unit_id": water_unit.pk,
                "month": month,
                "amount": "50.00",
                "reason": "Duplicate request",
            },
            content_type="application/json",
        )
        self.assertEqual(duplicate_waiver_response.status_code, 409)
        self.client.force_login(director)
        approval = self.client.post(
            f"/api/v1/property-officer/billing/waivers/{pre_invoice_waiver.pk}",
            data={
                "agency_id": self.nairobi.agency_id,
                "branch_id": self.nairobi.pk,
                "decision": "approved",
            },
            content_type="application/json",
        )
        self.assertEqual(approval.status_code, 200, approval.content)
        self.assertFalse(ArrearsWaiver.objects.filter(invoice__tenancy=tenancies[0]).exists())
        self.client.force_login(officer)

        draft_response = self.client.post(
            "/api/v1/property-officer/billing/draft",
            data={"month": month, "property_id": property_obj.pk},
            content_type="application/json",
        )
        self.assertEqual(draft_response.status_code, 200, draft_response.content)
        self.assertEqual(len(draft_response.json()["drafts"]), 1)
        self.assertEqual(len(draft_response.json()["held"]), 1)
        self.assertEqual(
            Invoice.objects.filter(tenancy__in=tenancies, status=Invoice.STATUS_DRAFT).count(),
            1,
        )
        readiness = self.client.get(
            "/api/v1/property-officer/billing",
            {"month": month, "property_id": property_obj.pk},
        )
        self.assertEqual(readiness.status_code, 200, readiness.content)
        held_row = next(row for row in readiness.json()["rows"] if row["unit_code"] == "BW-001")
        self.assertFalse(held_row["ready"])
        self.assertTrue(held_row["water_reading_required"])

        MeterReading.objects.create(
            unit=water_unit,
            meter="BW-METER",
            period=period_start,
            previous_value=Decimal("10"),
            current_value=Decimal("12"),
            submitted_by=self.create_user(
                email="billing-wizard-reader@example.com",
                phone="254712341102",
            ),
        )
        repeated = self.client.post(
            "/api/v1/property-officer/billing/draft",
            data={"month": month, "property_id": property_obj.pk},
            content_type="application/json",
        )
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(Invoice.objects.filter(tenancy__in=tenancies).count(), 2)

        pre_invoice_waiver.refresh_from_db()
        water_invoice = Invoice.objects.get(tenancy=tenancies[0])
        self.assertEqual(pre_invoice_waiver.invoice_id, water_invoice.pk)
        self.assertTrue(
            ArrearsWaiver.objects.filter(
                invoice=water_invoice,
                invoice_waiver_request=pre_invoice_waiver,
                amount=Decimal("100.00"),
            ).exists()
        )

        invoice = Invoice.objects.get(tenancy=tenancies[1])
        waiver_response = self.client.post(
            f"/api/v1/property-officer/billing/invoices/{invoice.pk}/waiver",
            data={"amount": "250.00", "reason": "Approved service interruption credit"},
            content_type="application/json",
        )
        self.assertEqual(waiver_response.status_code, 201, waiver_response.content)
        waiver = InvoiceWaiverRequest.objects.get(invoice=invoice)
        self.assertEqual(waiver.status, InvoiceWaiverRequest.STATUS_PENDING)
        self.assertIsNotNone(waiver.director_notified_at)
        self.assertTrue(SMSDelivery.objects.filter(template_name="director_invoice_waiver").exists())

        manager = self.create_user(
            email="billing-wizard-manager@example.com",
            phone="254712341103",
        )
        manager_membership, _ = AgencyMembership.objects.get_or_create(
            profile=manager.profile,
            agency=self.nairobi.agency,
        )
        manager_membership.roles.add(Group.objects.get(name="Branch Manager"))
        manager_membership.branches.add(self.nairobi)
        self.client.force_login(manager)
        decision = self.client.post(
            f"/api/v1/property-officer/billing/waivers/{waiver.pk}",
            data={"decision": "approved"},
            content_type="application/json",
        )
        self.assertEqual(decision.status_code, 403, decision.content)
        invoice.status = Invoice.STATUS_ISSUED
        invoice.save(update_fields=["status", "issued_at", "updated_at"])
        self.client.force_login(director)
        out_of_scope_decision = self.client.post(
            f"/api/v1/property-officer/billing/waivers/{waiver.pk}",
            data={
                "agency_id": self.nairobi.agency_id,
                "branch_id": self.nakuru.pk,
                "decision": "approved",
            },
            content_type="application/json",
        )
        self.assertEqual(out_of_scope_decision.status_code, 404)
        decision = self.client.post(
            f"/api/v1/property-officer/billing/waivers/{waiver.pk}",
            data={
                "agency_id": self.nairobi.agency_id,
                "branch_id": self.nairobi.pk,
                "decision": "approved",
            },
            content_type="application/json",
        )
        self.assertEqual(decision.status_code, 200, decision.content)
        waiver.refresh_from_db()
        self.assertEqual(waiver.status, InvoiceWaiverRequest.STATUS_APPROVED)
        self.assertTrue(ArrearsWaiver.objects.filter(invoice=invoice, amount=Decimal("250.00")).exists())

    def test_billing_cycle_prepares_and_publishes_in_nairobi_schedule(self):
        officer = self.create_user(
            email="scheduled-billing-officer@example.com",
            phone="254712341105",
        )
        officer_membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        officer_membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        officer_membership.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Billing Cycle Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="BC-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Billing Cycle Tenant",
            national_id_or_passport="ID-BILLING-CYCLE-1",
            phone="254712340099",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 8, 1),
            due_day=5,
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("1000.00"),
            effective_from=date(2026, 8, 1),
        )
        from zoneinfo import ZoneInfo

        nairobi = ZoneInfo("Africa/Nairobi")
        before_preparation = timezone.make_aware(datetime(2026, 7, 30, 8, 59), nairobi)
        preparation_time = timezone.make_aware(datetime(2026, 7, 30, 9, 0), nairobi)
        release_time = timezone.make_aware(datetime(2026, 8, 1, 0, 0), nairobi)
        output = StringIO()
        with patch(
            "users.tasks.timezone.localdate",
            return_value=before_preparation.date(),
        ), patch(
            "users.tasks.timezone.localtime",
            return_value=before_preparation,
        ):
            call_command("run_billing_cycle", stdout=output)
        self.assertFalse(Invoice.objects.filter(tenancy=tenancy).exists())
        with patch(
            "users.tasks.timezone.localdate",
            return_value=preparation_time.date(),
        ), patch(
            "users.tasks.timezone.localtime",
            return_value=preparation_time,
        ):
            call_command("run_billing_cycle", stdout=output)
        invoice = Invoice.objects.get(tenancy=tenancy)
        self.assertEqual(invoice.status, Invoice.STATUS_DRAFT)
        self.assertTrue(
            BillingNotification.objects.filter(
                property=property_obj,
                recipient=officer,
                notification_type=BillingNotification.TYPE_AGENT_DRAFTS,
            ).exists()
        )
        self.assertTrue(SMSDelivery.objects.filter(template_name="agent_billing_drafts").exists())
        InvoiceWaiverRequest.objects.create(
            invoice=invoice,
            amount=Decimal("100.00"),
            reason="Awaiting Director decision",
            requested_by=self.create_user(email="pending-waiver-agent@example.com"),
        )
        with patch(
            "users.tasks.timezone.localdate",
            return_value=release_time.date(),
        ), patch(
            "users.tasks.timezone.localtime",
            return_value=release_time,
        ), patch(
            "users.tasks.timezone.now",
            return_value=release_time,
        ):
            call_command("run_billing_cycle", stdout=output)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_ISSUED)
        self.assertEqual(invoice.issued_at, release_time)

    def test_billing_cycle_notifies_metered_water_caretakers_once(self):
        staff = self.create_user(
            email="metered-notification-staff@example.com",
            phone="254712341108",
        )
        caretaker = self.create_user(
            email="metered-notification-caretaker@example.com",
            phone="254712341109",
        )
        caretaker_membership, _ = AgencyMembership.objects.get_or_create(
            profile=caretaker.profile,
            agency=self.nairobi.agency,
        )
        caretaker_membership.roles.add(Group.objects.get(name="Caretaker"))
        caretaker_membership.branches.add(self.nairobi)
        period_start = date(2026, 8, 1)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Metered Notification Property",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=staff,
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="MN-001",
            unit_type="Studio",
            meters={"water": "MN-WATER"},
        )
        UtilityConfiguration.objects.create(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("10.0000"),
            effective_from=period_start,
            metadata={"meter": "MN-WATER"},
        )
        from zoneinfo import ZoneInfo

        run_at = timezone.make_aware(datetime(2026, 7, 30, 9, 0), ZoneInfo("Africa/Nairobi"))
        with patch("users.tasks.timezone.localdate", return_value=run_at.date()), patch(
            "users.tasks.timezone.localtime",
            return_value=run_at,
        ):
            from users.tasks import run_monthly_billing_cycle

            run_monthly_billing_cycle.run()
            run_monthly_billing_cycle.run()

        notification = BillingNotification.objects.get(
            property=property_obj,
            recipient=caretaker,
            period_start=period_start,
            notification_type=BillingNotification.TYPE_CARETAKER_READINGS,
        )
        self.assertIsNotNone(notification)
        self.assertEqual(
            SMSDelivery.objects.filter(template_name="caretaker_water_readings").count(),
            1,
        )

    def test_caretaker_must_save_all_metered_unit_readings_before_publish(self):
        staff = self.create_user(
            email="water-readings-staff@example.com",
            phone="254712341106",
        )
        caretaker = self.create_user(
            email="water-readings-caretaker@example.com",
            phone="254712341107",
        )
        caretaker_membership, _ = AgencyMembership.objects.get_or_create(
            profile=caretaker.profile,
            agency=self.nairobi.agency,
        )
        caretaker_membership.roles.add(Group.objects.get(name="Caretaker"))
        caretaker_membership.branches.add(self.nairobi)
        period_start = (timezone.localdate().replace(day=28) + timedelta(days=4)).replace(day=1)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Metered Caretaker Property",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=staff,
        )
        units = [
            Unit.objects.create(
                property=property_obj,
                code=f"MR-{index}",
                unit_type="Studio",
                meters={
                    "water": f"MR-METER-{index}",
                    **({"water_reading": "10.0000"} if index == 1 else {}),
                },
            )
            for index in (1, 2)
        ]
        for index, unit in enumerate(units, start=1):
            tenant = Tenant.objects.create(
                branch=self.nairobi,
                tenant_type=Tenant.INDIVIDUAL,
                name=f"Meter Reader Tenant {index}",
                national_id_or_passport=f"ID-MR-{index}",
                phone=f"254712340{index:03d}",
            )
            Tenancy.objects.create(
                unit=unit,
                tenant=tenant,
                tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
                start_date=period_start,
                due_day=5,
                base_water_meter_reading=None,
            )
            UnitCharge.objects.create(
                unit=unit,
                charge_type=UnitCharge.CHARGE_RENT,
                name="Monthly rent",
                amount=Decimal("1000.00"),
                effective_from=period_start,
            )
            UtilityConfiguration.objects.create(
                unit=unit,
                utility_type=UtilityConfiguration.UTILITY_WATER,
                name="Water",
                billing_method=UtilityConfiguration.METHOD_METERED,
                rate_per_unit=Decimal("12.0000"),
                effective_from=period_start,
                metadata={"meter": f"MR-METER-{index}"},
            )
        fixed_property = Property.objects.create(
            branch=self.nairobi,
            name="Fixed Utility Caretaker Property",
            property_type="Apartment",
            location="Nairobi",
        )
        PropertyCaretakerAssignment.objects.create(
            property=fixed_property,
            caretaker=caretaker,
            assigned_by=staff,
        )
        Unit.objects.create(
            property=fixed_property,
            code="FIXED-01",
            unit_type="Studio",
        )

        self.client.force_login(caretaker)
        readings = self.client.get("/api/v1/caretaker/water-readings")
        self.assertEqual(readings.status_code, 200, readings.content)
        self.assertEqual(
            [item["id"] for item in readings.json()["properties"]],
            [property_obj.pk],
        )
        property_readings = readings.json()["properties"][0]
        self.assertEqual(len(property_readings["units"]), 2)
        imported_baseline = next(
            item for item in property_readings["units"] if item["unit_id"] == units[0].pk
        )
        self.assertEqual(imported_baseline["previous_value"], "10.0000")
        self.assertEqual(imported_baseline["baseline_value"], "10.0000")
        dashboard = self.client.get("/api/v1/caretaker/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.content)
        fixed_dashboard_property = next(
            item for item in dashboard.json()["properties"] if item["id"] == fixed_property.pk
        )
        self.assertFalse(fixed_dashboard_property["has_metered_water"])

        partial = self.client.post(
            "/api/v1/caretaker/water-readings/drafts",
            data={
                "property_id": property_obj.pk,
                "period": period_start.isoformat(),
                "readings": [{"unit_id": units[0].pk, "current_value": "12.5"}],
            },
            content_type="application/json",
        )
        self.assertEqual(partial.status_code, 200, partial.content)
        self.assertEqual(MeterReadingDraft.objects.count(), 1)
        blocked = self.client.patch(
            "/api/v1/caretaker/water-readings/publish",
            data={"property_id": property_obj.pk, "period": period_start.isoformat()},
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 409, blocked.content)
        self.assertEqual(MeterReading.objects.count(), 0)

        complete = self.client.post(
            "/api/v1/caretaker/water-readings/drafts",
            data={
                "property_id": property_obj.pk,
                "period": period_start.isoformat(),
                "readings": [
                    {"unit_id": units[1].pk, "previous_value": "8.0", "current_value": "10.0"},
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(complete.status_code, 200, complete.content)
        missing_evidence = self.client.patch(
            "/api/v1/caretaker/water-readings/publish",
            data={"property_id": property_obj.pk, "period": period_start.isoformat()},
            content_type="application/json",
        )
        self.assertEqual(missing_evidence.status_code, 409, missing_evidence.content)

        invalid_photo = self.client.post(
            "/api/v1/caretaker/water-readings/photo",
            data={
                "property_id": property_obj.pk,
                "unit_id": units[0].pk,
                "period": period_start.isoformat(),
                "photo": SimpleUploadedFile("meter.txt", b"not an image", content_type="text/plain"),
            },
        )
        self.assertEqual(invalid_photo.status_code, 400, invalid_photo.content)

        with patch("users.views.upload_private_object") as store_photo:
            for unit in units:
                uploaded = self.client.post(
                    "/api/v1/caretaker/water-readings/photo",
                    data={
                        "property_id": property_obj.pk,
                        "unit_id": unit.pk,
                        "period": period_start.isoformat(),
                        "photo": SimpleUploadedFile(
                            f"meter-{unit.pk}.jpg",
                            b"\xff\xd8\xffmeter evidence",
                            content_type="image/jpeg",
                        ),
                    },
                )
                self.assertEqual(uploaded.status_code, 201, uploaded.content)
        self.assertEqual(store_photo.call_count, len(units))
        uploaded_readings = self.client.get("/api/v1/caretaker/water-readings").json()
        self.assertTrue(
            all(item["photo_uploaded"] for item in uploaded_readings["properties"][0]["units"])
        )

        changed = self.client.post(
            "/api/v1/caretaker/water-readings/drafts",
            data={
                "property_id": property_obj.pk,
                "period": period_start.isoformat(),
                "readings": [
                    {"unit_id": units[0].pk, "current_value": "13.0"},
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertFalse(
            MeterReadingDraft.objects.get(unit=units[0], period=period_start).photo_key
        )
        with patch("users.views.upload_private_object"), patch(
            "users.views.delete_private_object"
        ) as delete_photo:
            refreshed_photo = self.client.post(
                "/api/v1/caretaker/water-readings/photo",
                data={
                    "property_id": property_obj.pk,
                    "unit_id": units[0].pk,
                    "period": period_start.isoformat(),
                    "photo": SimpleUploadedFile(
                        "meter-updated.jpg",
                        b"\xff\xd8\xffupdated meter evidence",
                        content_type="image/jpeg",
                    ),
                },
            )
            self.assertEqual(delete_photo.call_count, 0)
        self.assertEqual(refreshed_photo.status_code, 201, refreshed_photo.content)

        with patch("users.tasks.draft_property_invoices.delay") as draft_task:
            published = self.client.patch(
                "/api/v1/caretaker/water-readings/publish",
                data={"property_id": property_obj.pk, "period": period_start.isoformat()},
                content_type="application/json",
            )
        self.assertEqual(published.status_code, 201, published.content)
        self.assertEqual(MeterReading.objects.filter(period=period_start).count(), 2)
        published_readings = MeterReading.objects.filter(period=period_start)
        self.assertTrue(all(reading.photo_key for reading in published_readings))
        self.assertTrue(
            all(reading.photo_metadata.get("content_type") == "image/jpeg" for reading in published_readings)
        )
        self.assertFalse(MeterReadingDraft.objects.exists())
        draft_task.assert_called_once_with(property_obj.pk, period_start.isoformat())
        from users.tasks import draft_property_invoices

        draft_result = draft_property_invoices.run(property_obj.pk, period_start.isoformat())
        self.assertEqual(draft_result["created"], 2)
        self.assertEqual(
            Invoice.objects.filter(
                tenancy__unit__property=property_obj,
                period_start=period_start,
                status=Invoice.STATUS_DRAFT,
            ).count(),
            2,
        )

    def test_monthly_invoice_command_includes_water_and_carries_balance_idempotently(self):
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Scheduled Billing Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="S-001",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
            meters={"water": "WM-SCHEDULED"},
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Scheduled Billing Tenant",
            national_id_or_passport="ID-SCHEDULED-1",
            phone="254712345699",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=5,
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("30000.00"),
            effective_from=date(2026, 1, 1),
        )
        UtilityConfiguration.objects.create(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("85.5000"),
            minimum_charge=Decimal("300.00"),
            effective_from=date(2026, 1, 1),
        )
        MeterReading.objects.create(
            unit=unit,
            meter="WM-SCHEDULED",
            period=date(2026, 2, 1),
            previous_value=Decimal(100),
            current_value=Decimal(110),
            submitted_by=self.create_user(email="scheduled-reader-1@example.com", phone="254712345670"),
        )
        output = StringIO()
        call_command("generate_monthly_invoices", period="2026-02-01", stdout=output)
        invoice = Invoice.objects.get(tenancy=tenancy)
        self.assertEqual(invoice.total, Decimal("30855.00"))
        self.assertEqual(invoice.lines.filter(charge_type="utility").count(), 1)
        call_command("generate_monthly_invoices", period="2026-02-01", stdout=output)
        self.assertEqual(Invoice.objects.filter(tenancy=tenancy).count(), 1)
        MeterReading.objects.create(
            unit=unit,
            meter="WM-SCHEDULED",
            period=date(2026, 3, 1),
            previous_value=Decimal(110),
            current_value=Decimal(111),
            submitted_by=self.create_user(phone="254712345671"),
        )
        call_command("generate_monthly_invoices", period="2026-03-01", stdout=output)
        march = Invoice.objects.get(tenancy=tenancy, period_start=date(2026, 3, 1))
        self.assertEqual(march.lines.get(charge_type="balance").amount, Decimal("30855.00"))
        self.assertEqual(march.total, Decimal("61155.00"))

    def test_monthly_invoice_carries_only_unpaid_and_unwaived_prior_charges(self):
        from users.billing import generate_monthly_invoice

        today = timezone.localdate()
        period_start = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        previous_end = period_start - timedelta(days=1)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Outstanding Balance Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="OB-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Outstanding Balance Tenant",
            national_id_or_passport="ID-OUTSTANDING-1",
            phone="254712345698",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=previous_end.replace(day=1),
            due_day=5,
        )
        prior_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-OUTSTANDING-PRIOR",
            period_start=previous_end.replace(day=1),
            period_end=previous_end,
            due_date=previous_end,
            total=Decimal("100.00"),
        )
        InvoiceLine.objects.create(
            invoice=prior_invoice,
            charge_type="arrears",
            description="Opening migration arrears",
            amount=Decimal("100.00"),
        )
        payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("60.00"),
            reference="OUTSTANDING-PAYMENT-1",
            source=Payment.SOURCE_MANUAL,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("60.00"),
        )
        PaymentAllocation.objects.create(
            payment=payment,
            invoice=prior_invoice,
            amount=Decimal("60.00"),
        )
        ArrearsWaiver.objects.create(
            tenancy=tenancy,
            invoice=prior_invoice,
            amount=Decimal("10.00"),
            reason="Approved test waiver",
            approved_by=self.create_user(phone="254712345697"),
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("200.00"),
            effective_from=period_start,
        )

        invoice, was_created = generate_monthly_invoice(tenancy, period_start)

        self.assertTrue(was_created)
        self.assertEqual(invoice.lines.get(charge_type="balance").amount, Decimal("30.00"))
        self.assertEqual(invoice.total, Decimal("230.00"))

    def test_notice_month_draft_invoice_is_reduced_to_metered_water_only(self):
        from users.billing import generate_monthly_invoice

        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Notice Month Billing Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="NM-001",
            unit_type="Studio",
            meters={"water": "WM-NOTICE"},
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Notice Month Tenant",
            national_id_or_passport="ID-NOTICE-MONTH-1",
            phone="254712345696",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=5,
        )
        reviewer = self.create_user(email="notice-reviewer@example.com", phone="254712345695")
        notice_date = date(2026, 2, 20)
        MoveOutNotice.objects.create(
            tenancy=tenancy,
            requested_by=reviewer,
            proposed_move_out_date=notice_date,
            status=MoveOutNotice.STATUS_ACCEPTED,
            reviewed_by=reviewer,
            reviewed_at=timezone.now(),
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("30000.00"),
            effective_from=date(2026, 1, 1),
        )
        UtilityConfiguration.objects.create(
            unit=unit,
            utility_type=UtilityConfiguration.UTILITY_WATER,
            name="Metered water",
            billing_method=UtilityConfiguration.METHOD_METERED,
            rate_per_unit=Decimal("12.0000"),
            minimum_charge=Decimal("0.00"),
            effective_from=date(2026, 1, 1),
            metadata={"meter": "WM-NOTICE"},
        )
        MeterReading.objects.create(
            unit=unit,
            meter="WM-NOTICE",
            period=date(2026, 2, 1),
            previous_value=Decimal("100.0000"),
            current_value=Decimal("110.0000"),
            submitted_by=reviewer,
        )
        invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-NOTICE-MONTH-1",
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            due_date=date(2026, 2, 5),
            status=Invoice.STATUS_DRAFT,
            total=Decimal("30000.00"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            charge_type="rent",
            description="Monthly rent",
            amount=Decimal("30000.00"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            charge_type="utility",
            description="Metered water",
            amount=Decimal("120.00"),
        )

        updated, was_created = generate_monthly_invoice(
            tenancy,
            date(2026, 2, 1),
            status=Invoice.STATUS_DRAFT,
        )

        self.assertFalse(was_created)
        self.assertEqual(updated.pk, invoice.pk)
        self.assertEqual(updated.status, Invoice.STATUS_DRAFT)
        self.assertEqual(updated.total, Decimal("120.00"))
        self.assertEqual(
            list(updated.lines.values_list("description", flat=True)),
            ["Metered water"],
        )

        no_water_unit = Unit.objects.create(
            property=property_obj,
            code="NM-002",
            unit_type="Studio",
        )
        no_water_tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="No Water Notice Tenant",
            national_id_or_passport="ID-NOTICE-MONTH-2",
            phone="254712345694",
        )
        no_water_tenancy = Tenancy.objects.create(
            unit=no_water_unit,
            tenant=no_water_tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=5,
        )
        MoveOutNotice.objects.create(
            tenancy=no_water_tenancy,
            requested_by=reviewer,
            proposed_move_out_date=notice_date,
            status=MoveOutNotice.STATUS_ACCEPTED,
            reviewed_by=reviewer,
            reviewed_at=timezone.now(),
        )
        no_water_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=no_water_tenancy,
            number="INV-NOTICE-MONTH-2",
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            due_date=date(2026, 2, 5),
            status=Invoice.STATUS_DRAFT,
            total=Decimal("30000.00"),
        )
        InvoiceLine.objects.create(
            invoice=no_water_invoice,
            charge_type="rent",
            description="Monthly rent",
            amount=Decimal("30000.00"),
        )

        voided, _ = generate_monthly_invoice(
            no_water_tenancy,
            date(2026, 2, 1),
            status=Invoice.STATUS_DRAFT,
        )

        self.assertEqual(voided.status, Invoice.STATUS_VOID)
        self.assertEqual(voided.total, Decimal("0.00"))
        self.assertFalse(voided.lines.exists())

    def test_arrears_reminder_planner_uses_escalating_three_day_schedule_and_never_dispatches(self):
        today = timezone.localdate()
        due_date = today + timedelta(days=3)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Reminder Planning Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="RP-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Reminder Planning Tenant",
            national_id_or_passport="ID-REMINDER-PLAN-1",
            phone="254712345690",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=due_date - timedelta(days=30),
            due_day=due_date.day,
        )
        invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-REMINDER-PLAN-1",
            period_start=due_date - timedelta(days=30),
            period_end=due_date,
            due_date=due_date,
            total=Decimal("1000.00"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            charge_type="rent",
            description="Monthly rent",
            amount=Decimal("1000.00"),
        )
        ArrearsPolicy.objects.create(
            branch=self.nairobi,
            grace_days=0,
            reminder_after_days=7,
            penalty_rate=Decimal("0.0000"),
        )
        partial_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("400.00"),
            reference="REMINDER-PLAN-PARTIAL",
            source=Payment.SOURCE_MANUAL,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("400.00"),
        )
        PaymentAllocation.objects.create(
            payment=partial_payment,
            invoice=invoice,
            amount=Decimal("400.00"),
        )

        output = StringIO()
        call_command("plan_arrears_reminders", as_of=today.isoformat(), stdout=output)
        call_command("plan_arrears_reminders", as_of=today.isoformat(), stdout=output)
        self.assertEqual(ArrearsReminder.objects.count(), 2)
        self.assertEqual(
            set(ArrearsReminder.objects.values_list("channel", flat=True)),
            {ArrearsReminder.CHANNEL_SMS, ArrearsReminder.CHANNEL_WHATSAPP},
        )
        self.assertEqual(
            set(ArrearsReminder.objects.values_list("amount_due", flat=True)),
            {Decimal("600.00")},
        )
        self.assertTrue(
            ArrearsReminder.objects.filter(
                scheduled_for=today,
                status=ArrearsReminder.STATUS_PLANNED,
                stage=ArrearsReminder.STAGE_BEFORE_DUE,
            ).exists()
        )
        self.assertEqual(SMSDelivery.objects.count(), 0)
        self.assertEqual(
            AuditLog.objects.filter(action="arrears_reminder.planned").count(),
            2,
        )

        due_day = due_date
        call_command("plan_arrears_reminders", as_of=due_day.isoformat(), stdout=output)
        first_follow_up = due_date + timedelta(days=3)
        call_command("plan_arrears_reminders", as_of=first_follow_up.isoformat(), stdout=output)
        later_follow_up = due_date + timedelta(days=33)
        call_command("plan_arrears_reminders", as_of=later_follow_up.isoformat(), stdout=output)
        self.assertEqual(
            ArrearsReminder.objects.filter(
                scheduled_for=due_day,
                status=ArrearsReminder.STATUS_PLANNED,
            ).count(),
            2,
        )
        self.assertEqual(
            set(ArrearsReminder.objects.values_list("stage", flat=True)),
            {
                ArrearsReminder.STAGE_BEFORE_DUE,
                ArrearsReminder.STAGE_DUE_TODAY,
                ArrearsReminder.STAGE_OVERDUE_1_30,
                ArrearsReminder.STAGE_OVERDUE_31_60,
            },
        )
        self.assertEqual(ArrearsReminder.objects.count(), 8)
        planned_messages = set(
            ArrearsReminder.objects.values_list("message", flat=True)
        )
        self.assertEqual(len(planned_messages), 4)
        self.assertTrue(any("due on" in message for message in planned_messages))
        self.assertTrue(any("due today" in message for message in planned_messages))
        self.assertTrue(any("days overdue" in message for message in planned_messages))
        self.assertTrue(any("remains unpaid after" in message for message in planned_messages))

        remaining_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("600.00"),
            reference="REMINDER-PLAN-REMAINING",
            source=Payment.SOURCE_MANUAL,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("600.00"),
        )
        PaymentAllocation.objects.create(
            payment=remaining_payment,
            invoice=invoice,
            amount=Decimal("600.00"),
        )
        call_command(
            "plan_arrears_reminders",
            as_of=(later_follow_up + timedelta(days=1)).isoformat(),
            stdout=output,
        )
        self.assertEqual(
            ArrearsReminder.objects.filter(status=ArrearsReminder.STATUS_PLANNED).count(),
            0,
        )
        self.assertEqual(
            ArrearsReminder.objects.filter(status=ArrearsReminder.STATUS_CANCELLED).count(),
            8,
        )
        self.assertEqual(SMSDelivery.objects.count(), 0)
        self.assertEqual(
            AuditLog.objects.filter(action="arrears_reminder.cancelled").count(),
            8,
        )

    def test_arrears_ageing_penalty_manager_waiver_and_demand_letter(self):
        user = self.create_user(phone="254712345672")
        user.groups.add(Group.objects.get(name="Branch Manager"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Arrears Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="A-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Arrears Tenant",
            national_id_or_passport="ID-ARREARS-1",
            phone="254712345673",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=1,
        )
        invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-ARREARS-1",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            due_date=date(2026, 1, 1),
            total=Decimal("10000.00"),
        )
        ArrearsPolicy.objects.create(
            branch=self.nairobi,
            grace_days=5,
            penalty_rate=Decimal("10.0000"),
            demand_after_days=30,
        )
        arrears = self.client.get("/api/v1/arrears?as_of=2026-01-20")
        self.assertEqual(arrears.status_code, 200)
        self.assertEqual(arrears.json()["arrears"][0]["bucket"], "1-30")
        self.assertEqual(arrears.json()["arrears"][0]["penalty"], "1000.00")
        waiver = self.client.post(
            f"/api/v1/invoices/{invoice.pk}/waiver",
            data={"amount": "2500.00", "reason": "Approved hardship waiver"},
            content_type="application/json",
        )
        self.assertEqual(waiver.status_code, 201)
        demand = self.client.post(
            f"/api/v1/invoices/{invoice.pk}/demand-letter",
            data={},
            content_type="application/json",
        )
        self.assertEqual(demand.status_code, 201)
        repeated = self.client.post(
            f"/api/v1/invoices/{invoice.pk}/demand-letter",
            data={},
            content_type="application/json",
        )
        self.assertEqual(repeated.status_code, 200)

    def test_payment_plan_tracks_instalments_balance_missed_and_completion(self):
        manager = self.create_user(phone="254712345674")
        manager.groups.add(Group.objects.get(name="Branch Manager"))
        manager.profile.branches.add(self.nairobi)
        self.client.force_login(manager)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Payment Plan Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="P-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Payment Plan Tenant",
            national_id_or_passport="ID-PLAN-1",
            phone="254712345675",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=1,
        )
        created = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/payment-plans/create",
            data={
                "total_amount": "2500.00",
                "instalment_amount": "1000.00",
                "start_date": "2026-02-01",
                "reason": "Approved arrears repayment agreement",
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(len(created.json()["payment_plan"]["instalments"]), 3)
        self.assertEqual(created.json()["payment_plan"]["instalments"][2]["amount_due"], "500.00")
        plan = ArrearsPaymentPlan.objects.get(tenancy=tenancy)
        first = plan.instalments.get(sequence=1)
        paid = self.client.post(
            f"/api/v1/payment-plan-instalments/{first.pk}/pay",
            data={"amount": "1000.00"},
            content_type="application/json",
        )
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(paid.json()["payment_plan"]["balance"], "1500.00")
        overdue = plan.instalments.get(sequence=2)
        overdue.status = "missed"
        overdue.save(update_fields=["status"])
        self.assertEqual(self.client.get("/api/v1/payment-plans").status_code, 200)
        for item in plan.instalments.exclude(pk=first.pk):
            self.client.post(
                f"/api/v1/payment-plan-instalments/{item.pk}/pay",
                data={"amount": str(item.amount_due)},
                content_type="application/json",
            )
        self.assertEqual(ArrearsPaymentPlan.objects.get(pk=plan.pk).status, "completed")

    def test_mpesa_callback_persists_raw_payload_and_is_idempotent(self):
        payload = {
            "TransID": "QAB123XYZ",
            "TransAmount": "1500.00",
            "MSISDN": "254712345678",
            "BillRefNumber": "TENANCY-1",
            "TransTime": "20261102200000",
        }
        first = self.client.post(
            "/api/v1/payments/mpesa/callback",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 202)
        callback = MpesaCallback.objects.get(transaction_code="QAB123XYZ")
        self.assertEqual(callback.status, MpesaCallback.STATUS_PROCESSED)
        self.assertEqual(callback.raw_payload, payload)
        repeated = self.client.post(
            "/api/v1/payments/mpesa/callback",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["callback"]["id"], callback.pk)
        self.assertEqual(MpesaCallback.objects.count(), 1)
        invalid = self.client.post(
            "/api/v1/payments/mpesa/callback",
            data={"TransID": "QAB-INVALID", "TransAmount": "-1"},
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            MpesaCallback.objects.get(transaction_code="QAB-INVALID").status,
            MpesaCallback.STATUS_FAILED,
        )

    def test_payment_allocates_oldest_invoices_and_records_tenant_credit(self):
        user = self.create_user(email="accountant@example.com")
        user.groups.add(Group.objects.get(name="Accountant"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Payment Allocation Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="P-001", unit_type="Studio")
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            charge_code="rent",
            name="Monthly rent",
            amount="1000.00",
            effective_from=date(2026, 1, 1),
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Allocation Tenant",
            national_id_or_passport="ID-ALLOC-1",
            phone="254712345679",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=1,
        )
        for month in (1, 2):
            self.client.post(
                f"/api/v1/tenancies/{tenancy.pk}/invoices/create",
                data={
                    "period_start": f"2026-{month:02d}-01",
                    "period_end": f"2026-{month:02d}-28",
                },
                content_type="application/json",
            )
        payment = self.client.post(
            "/api/v1/payments/create",
            data={
                "tenant_id": tenant.pk,
                "amount": "2500.00",
                "reference": "PAY-ALLOC-1",
                "source": "mpesa",
            },
            content_type="application/json",
        )
        self.assertEqual(payment.status_code, 201)
        self.assertEqual(payment.json()["payment"]["allocated_amount"], "2000.00")
        self.assertEqual(payment.json()["payment"]["credit_amount"], "500.00")
        self.assertEqual(PaymentAllocation.objects.count(), 2)
        self.assertEqual(TenantCredit.objects.get(tenant=tenant).remaining_amount, Decimal("500.00"))
        self.assertEqual(Payment.objects.get(reference="PAY-ALLOC-1").credit_amount, Decimal("500.00"))
        receipt = Receipt.objects.get(payment__reference="PAY-ALLOC-1")
        self.assertEqual(receipt.amount, Decimal("2500.00"))
        self.assertEqual(receipt.balance_after, Decimal("0.00"))
        downloaded = self.client.get(f"/api/v1/payments/{payment.json()['payment']['id']}/receipt")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(
            downloaded["Content-Disposition"],
            f'attachment; filename="{receipt.number}.json"',
        )
        self.assertEqual(downloaded.json()["receipt"]["number"], receipt.number)

    def test_unmatched_payment_is_queued_with_non_binding_suggestion_and_can_be_allocated(self):
        user = self.create_user(email="manager@example.com")
        user.groups.add(Group.objects.get(name="Branch Manager"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Unmatched Payment Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="U-001", unit_type="Studio")
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            charge_code="rent",
            name="Monthly rent",
            amount="1000.00",
            effective_from=date(2026, 1, 1),
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Unmatched Tenant",
            national_id_or_passport="ID-UNMATCHED-1",
            phone="254712345680",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=1,
        )
        invoice_response = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/invoices/create",
            data={"period_start": "2026-01-01", "period_end": "2026-01-31"},
            content_type="application/json",
        )
        self.assertEqual(invoice_response.status_code, 201)
        invoice_id = invoice_response.json()["invoice"]["id"]
        payment = self.client.post(
            "/api/v1/payments/create",
            data={
                "amount": "1000.00",
                "reference": "PAY-UNMATCHED-1",
                "source": "mpesa",
                "account_reference": tenant.national_id_or_passport,
            },
            content_type="application/json",
        )
        self.assertEqual(payment.status_code, 201)
        self.assertEqual(payment.json()["payment"]["status"], Payment.STATUS_UNMATCHED)
        queue = self.client.get("/api/v1/payments/unmatched")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.json()["payments"][0]["suggestions"][0]["tenant_id"], tenant.pk)
        allocated = self.client.post(
            f"/api/v1/payments/{payment.json()['payment']['id']}/allocate",
            data={"invoice_id": invoice_id, "amount": "1000.00"},
            content_type="application/json",
        )
        self.assertEqual(allocated.status_code, 200)
        self.assertEqual(allocated.json()["payment"]["status"], Payment.STATUS_ALLOCATED)
        self.assertEqual(allocated.json()["payment"]["tenant_id"], tenant.pk)
        self.assertEqual(Receipt.objects.get(payment_id=payment.json()["payment"]["id"]).amount, Decimal("1000.00"))

    def test_bank_statement_import_validates_rows_and_reports_reconciliation(self):
        user = self.create_user(email="bank-accountant@example.com")
        user.groups.add(Group.objects.get(name="Accountant"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        upload = SimpleUploadedFile(
            "statement.csv",
            (
                b"transaction_date,reference,amount,account_reference\n"
                b"2026-02-01,BANK-001,1500.00,UNKNOWN\n"
                b"2026-02-02,BANK-002,-2.00,UNKNOWN\n"
            ),
            content_type="text/csv",
        )
        response = self.client.post("/api/v1/bank-statements/import", data={"file": upload})
        self.assertEqual(response.status_code, 201)
        statement = BankStatementImport.objects.get(pk=response.json()["statement"]["id"])
        self.assertEqual(statement.valid_rows, 1)
        self.assertEqual(statement.invalid_rows, 1)
        self.assertEqual(
            BankStatementRow.objects.filter(
                statement=statement,
                status=BankStatementRow.STATUS_REVIEW,
            ).count(),
            1,
        )
        report = self.client.get(f"/api/v1/bank-statements/{statement.pk}/reconciliation")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["row_counts"]["invalid"], 1)
        self.assertEqual(report.json()["totals"]["valid_amount"], "1500.00")

    def test_cash_and_cheque_payments_are_authorized_receipted_and_reported(self):
        user = self.create_user(email="cashier-manager@example.com")
        user.groups.add(Group.objects.get(name="Branch Manager"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Offline Payment Property",
            property_type="Apartment",
            location="Kilimani",
        )
        Unit.objects.create(property=property_obj, code="OFF-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Offline Tenant",
            national_id_or_passport="ID-OFFLINE-1",
            phone="254712345681",
        )
        cash = self.client.post(
            "/api/v1/payments/offline/create",
            data={
                "tenant_id": tenant.pk,
                "amount": "500.00",
                "reference": "CASH-001",
                "source": "cash",
                "collected_by": user.pk,
            },
            content_type="application/json",
        )
        self.assertEqual(cash.status_code, 201)
        self.assertEqual(cash.json()["payment"]["instrument_status"], Payment.INSTRUMENT_COLLECTED)
        self.assertTrue(Receipt.objects.filter(payment__reference="CASH-001").exists())
        report = self.client.get("/api/v1/payments/cash-report")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["total"], "500.00")
        cheque = self.client.post(
            "/api/v1/payments/offline/create",
            data={
                "tenant_id": tenant.pk,
                "amount": "750.00",
                "reference": "CHEQUE-001",
                "source": "cheque",
                "cheque_number": "CHQ-001",
                "cheque_bank": "Demo Bank",
            },
            content_type="application/json",
        )
        self.assertEqual(cheque.status_code, 201)
        cleared = self.client.post(
            f"/api/v1/payments/{cheque.json()['payment']['id']}/cheque-status",
            data={"status": "cleared"},
            content_type="application/json",
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["payment"]["instrument_status"], Payment.INSTRUMENT_CLEARED)

    def test_daily_reconciliation_reports_channel_totals(self):
        user = self.create_user(email="reconciliation-manager@example.com")
        user.groups.add(Group.objects.get(name="Branch Manager"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Reconciliation Tenant",
            national_id_or_passport="ID-RECON-1",
            phone="254712345683",
        )
        received_at = timezone.now()
        reconciliation_date = timezone.localdate(received_at)
        Payment.objects.create(
            tenant=tenant,
            amount="1000.00",
            reference="RECON-MPESA-1",
            source=Payment.SOURCE_MPESA,
            received_at=received_at,
            status=Payment.STATUS_ALLOCATED,
            allocated_amount="1000.00",
        )
        Payment.objects.create(
            tenant=tenant,
            amount="750.00",
            reference="RECON-CASH-1",
            source=Payment.SOURCE_CASH,
            received_at=received_at,
            status=Payment.STATUS_PARTIALLY_ALLOCATED,
            allocated_amount="250.00",
        )
        Payment.objects.create(
            tenant=tenant,
            amount="500.00",
            reference="RECON-CHEQUE-1",
            source=Payment.SOURCE_CHEQUE,
            received_at=received_at,
            instrument_status=Payment.INSTRUMENT_BOUNCED,
            status=Payment.STATUS_UNMATCHED,
        )
        response = self.client.get(
            f"/api/v1/payments/daily-reconciliation?from={reconciliation_date.isoformat()}&to={reconciliation_date.isoformat()}"
        )
        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertEqual(report["totals"]["received"], "2250.00")
        self.assertEqual(report["totals"]["matched"], "1250.00")
        self.assertEqual(report["totals"]["unmatched"], "500.00")
        self.assertEqual(report["totals"]["reversed"], "500.00")
        self.assertEqual(report["totals"]["allocated"], "1000.00")
        self.assertEqual(report["channels"]["cash"]["unmatched"], "500.00")
        self.assertEqual(report["channels"]["cheque"]["reversed"], "500.00")

    def test_deposit_ledger_tracks_refunds_and_configured_top_up(self):
        user = self.create_user(email="deposit-manager@example.com")
        user.groups.add(Group.objects.get(name="Branch Manager"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Deposit Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="DEP-001", unit_type="Studio")
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Deposit Tenant",
            national_id_or_passport="ID-DEPOSIT-1",
            phone="254712345682",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=1,
        )
        received = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/deposit/entries",
            data={
                "entry_type": "receipt",
                "amount": "10000.00",
                "target_amount": "15000.00",
                "reference": "DEP-RECEIPT-1",
                "description": "Initial refundable deposit",
            },
            content_type="application/json",
        )
        self.assertEqual(received.status_code, 201)
        self.assertEqual(received.json()["deposit"]["balance"], "10000.00")
        self.assertEqual(received.json()["deposit"]["top_up_amount"], "5000.00")
        refunded = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/deposit/entries",
            data={
                "entry_type": "refund",
                "amount": "2000.00",
                "reference": "DEP-REFUND-1",
            },
            content_type="application/json",
        )
        self.assertEqual(refunded.status_code, 201)
        self.assertEqual(refunded.json()["deposit"]["balance"], "8000.00")
        self.assertEqual(DepositAccount.objects.get(tenancy=tenancy).entries.count(), 2)
        self.assertEqual(DepositEntry.objects.filter(account__tenancy=tenancy).count(), 2)

    def test_utility_configuration_supports_fixed_metered_and_effective_history(self):
        user = self.create_user(phone="254712345695")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Utilities Property",
            property_type="Apartment",
            location="Lavington",
        )
        unit = Unit.objects.create(property=property_obj, code="U-001", unit_type="Studio")

        fixed = self.client.post(
            f"/api/v1/units/{unit.pk}/utilities/create",
            data={
                "utility_type": "electricity",
                "name": "Electricity",
                "billing_method": "fixed",
                "fixed_amount": "1200.00",
                "effective_from": "2026-01-01",
            },
            content_type="application/json",
        )
        self.assertEqual(fixed.status_code, 201)
        metered = self.client.post(
            f"/api/v1/units/{unit.pk}/utilities/create",
            data={
                "utility_type": "water",
                "name": "Water",
                "billing_method": "metered",
                "rate_per_unit": "85.5000",
                "minimum_charge": "300.00",
                "effective_from": "2026-01-01",
            },
            content_type="application/json",
        )
        self.assertEqual(metered.status_code, 201)
        self.assertEqual(
            self.client.get(f"/api/v1/units/{unit.pk}/utilities").json()["utilities"][1]["rate_per_unit"],
            "85.5000",
        )
        invalid = self.client.post(
            f"/api/v1/units/{unit.pk}/utilities/create",
            data={
                "utility_type": "water",
                "name": "Invalid water",
                "billing_method": "metered",
                "fixed_amount": "500.00",
                "effective_from": "2026-01-01",
            },
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(UtilityConfiguration.objects.filter(unit=unit).count(), 2)

    def test_recurring_charge_codes_allow_multiple_charge_categories(self):
        user = self.create_user(phone="254712345696")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Recurring Charges Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="R-001", unit_type="Studio")
        for code, name, amount in (
            ("service", "Service charge", "1500.00"),
            ("security", "Security", "500.00"),
            ("garbage", "Garbage", "200.00"),
        ):
            response = self.client.post(
                f"/api/v1/units/{unit.pk}/charges/create",
                data={
                    "charge_type": "recurring",
                    "charge_code": code,
                    "name": name,
                    "amount": amount,
                    "effective_from": "2026-01-01",
                },
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)
        self.assertEqual(UnitCharge.objects.filter(unit=unit, charge_type="recurring").count(), 3)

    def test_meter_readings_validate_values_evidence_and_scope(self):
        user = self.create_user(phone="254712345697")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Meter Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="M-001", unit_type="Studio")
        created = self.client.post(
            f"/api/v1/units/{unit.pk}/meter-readings/create",
            data={
                "meter": "WM-001",
                "period": "2026-02-01",
                "previous_value": "100.5000",
                "current_value": "125.7500",
                "photo_key": "meters/WM-001/2026-02.jpg",
                "photo_metadata": {"content_type": "image/jpeg", "size_bytes": 2048},
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["reading"]["current_value"], "125.7500")
        self.assertEqual(MeterReading.objects.get(unit=unit).submitted_by, user)
        self.assertEqual(self.client.get(f"/api/v1/units/{unit.pk}/meter-readings").status_code, 200)
        decreasing = self.client.post(
            f"/api/v1/units/{unit.pk}/meter-readings/create",
            data={
                "meter": "WM-001",
                "period": "2026-03-01",
                "previous_value": "130",
                "current_value": "129",
            },
            content_type="application/json",
        )
        self.assertEqual(decreasing.status_code, 400)
        bad_photo = self.client.post(
            f"/api/v1/units/{unit.pk}/meter-readings/create",
            data={
                "meter": "WM-001",
                "period": "2026-04-01",
                "previous_value": "125",
                "current_value": "130",
                "photo_key": "meters/WM-001/2026-04.jpg",
                "photo_metadata": {"content_type": "application/pdf", "size_bytes": 2048},
            },
            content_type="application/json",
        )
        self.assertEqual(bad_photo.status_code, 400)
        bad_size = self.client.post(
            f"/api/v1/units/{unit.pk}/meter-readings/create",
            data={
                "meter": "WM-001",
                "period": "2026-05-01",
                "previous_value": "130",
                "current_value": "135",
                "photo_key": "meters/WM-001/2026-05.jpg",
                "photo_metadata": {"content_type": "image/jpeg", "size_bytes": "not-a-number"},
            },
            content_type="application/json",
        )
        self.assertEqual(bad_size.status_code, 400)

    def test_water_bill_calculation_uses_rate_and_minimum_charge(self):
        user = self.create_user(phone="254712345698")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Water Billing Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(property=property_obj, code="W-001", unit_type="Studio")
        utility = UtilityConfiguration.objects.create(
            unit=unit,
            utility_type="water",
            name="Water",
            billing_method="metered",
            rate_per_unit=Decimal("85.5000"),
            minimum_charge=Decimal("300.00"),
            effective_from=date(2026, 1, 1),
        )
        reading = MeterReading.objects.create(
            unit=unit,
            meter="WM-001",
            period=date(2026, 2, 1),
            previous_value=Decimal("100.0000"),
            current_value=Decimal("102.0000"),
            submitted_by=user,
        )
        minimum = self.client.post(
            f"/api/v1/units/{unit.pk}/water-bill/calculate",
            data={"meter": "WM-001", "period": "2026-02-01"},
            content_type="application/json",
        )
        self.assertEqual(minimum.status_code, 200)
        self.assertEqual(minimum.json()["water_bill"]["usage"], "2.0000")
        self.assertEqual(minimum.json()["water_bill"]["calculated_charge"], "300.00")
        reading.current_value = Decimal("110.0000")
        reading.save(update_fields=["current_value"])
        rate_based = self.client.post(
            f"/api/v1/units/{unit.pk}/water-bill/calculate",
            data={"meter": "WM-001", "period": "2026-02-01"},
            content_type="application/json",
        )
        self.assertEqual(rate_based.status_code, 200)
        self.assertEqual(rate_based.json()["water_bill"]["calculated_charge"], "855.00")
        self.assertTrue(AuditLog.objects.filter(action="water_bill.calculated").exists())
        missing_reading = self.client.post(
            f"/api/v1/units/{unit.pk}/water-bill/calculate",
            data={"meter": "WM-001", "period": "2026-03-01"},
            content_type="application/json",
        )
        self.assertEqual(missing_reading.status_code, 400)
        self.assertEqual(utility.utility_type, "water")

    def test_unit_charge_api_preserves_history_and_supports_future_changes(self):
        user = self.create_user(phone="254712345688")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Charges Property",
            property_type="Apartment",
            location="Kileleshwa",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="C-001",
            unit_type="Two bedroom",
            status=Unit.STATUS_VACANT,
        )

        current = self.client.post(
            f"/api/v1/units/{unit.pk}/charges/create",
            data={
                "charge_type": UnitCharge.CHARGE_RENT,
                "name": "Monthly rent",
                "amount": "45000.00",
                "effective_from": "2026-01-01",
            },
            content_type="application/json",
        )
        self.assertEqual(current.status_code, 201)
        future = self.client.post(
            f"/api/v1/units/{unit.pk}/charges/create",
            data={
                "charge_type": UnitCharge.CHARGE_RENT,
                "name": "Monthly rent",
                "amount": "50000.00",
                "effective_from": "2027-01-01",
            },
            content_type="application/json",
        )
        self.assertEqual(future.status_code, 201)
        charges = list(UnitCharge.objects.filter(unit=unit).order_by("effective_from"))
        self.assertEqual(len(charges), 2)
        self.assertEqual(charges[0].amount, Decimal("45000.00"))
        self.assertEqual(charges[0].effective_from.isoformat(), "2026-01-01")
        self.assertEqual(charges[1].amount, Decimal("50000.00"))
        self.assertEqual(
            self.client.get(f"/api/v1/units/{unit.pk}/charges").json()["charges"][0]["amount"],
            "45000.00",
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="unit_charge.created", entity="UnitCharge", user=user
            ).exists()
        )

    def test_unit_charge_api_rejects_overlaps_and_invalid_dates(self):
        user = self.create_user(phone="254712345689")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Overlap Property",
            property_type="Apartment",
            location="Runda",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="O-001",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
        )
        first = UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_DEPOSIT,
            name="Security deposit",
            amount=Decimal("30000.00"),
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
        )
        self.assertIsNotNone(first.pk)
        overlap = self.client.post(
            f"/api/v1/units/{unit.pk}/charges/create",
            data={
                "charge_type": UnitCharge.CHARGE_DEPOSIT,
                "name": "Security deposit",
                "amount": "35000.00",
                "effective_from": "2026-06-01",
            },
            content_type="application/json",
        )
        self.assertEqual(overlap.status_code, 400)
        self.assertEqual(UnitCharge.objects.filter(unit=unit).count(), 1)

    def test_unit_reservation_blocks_competing_reservations_and_releases_listing(self):
        user = self.create_user(phone="254712345690")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Reservations Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="R-001",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
            listing_enabled=True,
        )
        applicant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Reservation Applicant",
            national_id_or_passport="ID-RESERVE-1",
            phone="254712345691",
        )

        created = self.client.post(
            f"/api/v1/units/{unit.pk}/reservations/create",
            data={
                "applicant_id": applicant.pk,
                "expires_at": "2099-01-01T12:00:00+00:00",
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        reservation = UnitReservation.objects.get(unit=unit)
        unit.refresh_from_db()
        self.assertEqual(reservation.status, UnitReservation.STATUS_ACTIVE)
        self.assertFalse(unit.listing_enabled)

        conflict = self.client.post(
            f"/api/v1/units/{unit.pk}/reservations/create",
            data={
                "applicant_id": applicant.pk,
                "expires_at": "2099-01-02T12:00:00+00:00",
            },
            content_type="application/json",
        )
        self.assertEqual(conflict.status_code, 409)

        released = self.client.post(
            f"/api/v1/unit-reservations/{reservation.pk}/release",
            content_type="application/json",
        )
        self.assertEqual(released.status_code, 200)
        reservation.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(reservation.status, UnitReservation.STATUS_RELEASED)
        self.assertTrue(unit.listing_enabled)
        self.assertTrue(
            AuditLog.objects.filter(
                action="unit_reservation.released",
                entity="UnitReservation",
                user=user,
            ).exists()
        )

    def test_expired_reservation_is_reconciled_and_unit_becomes_listable(self):
        user = self.create_user(phone="254712345692")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Expired Reservation Property",
            property_type="Apartment",
            location="Westlands",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="R-002",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
            listing_enabled=True,
        )
        applicant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Expired Applicant",
            national_id_or_passport="ID-RESERVE-2",
            phone="254712345693",
        )
        reservation = UnitReservation.objects.create(
            unit=unit,
            applicant=applicant,
            expires_at=timezone.now() - timedelta(minutes=1),
            status=UnitReservation.STATUS_ACTIVE,
            listing_enabled_before_reservation=True,
            created_by=user,
        )
        unit.listing_enabled = False
        unit.save(update_fields=["listing_enabled"])

        response = self.client.get("/api/v1/unit-reservations")
        self.assertEqual(response.status_code, 200)
        reservation.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(reservation.status, UnitReservation.STATUS_EXPIRED)
        self.assertTrue(unit.listing_enabled)
        self.assertTrue(
            AuditLog.objects.filter(
                action="unit_reservation.expired",
                entity="UnitReservation",
                user=user,
            ).exists()
        )

    def test_tenancy_api_validates_terms_occupancy_and_reservation_release(self):
        user = self.create_user(phone="254712345694")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Tenancy Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="T-001",
            unit_type="One bedroom",
            status=Unit.STATUS_VACANT,
            listing_enabled=True,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Tenancy Applicant",
            national_id_or_passport="ID-TENANCY-1",
            phone="254712345695",
        )
        reservation = UnitReservation.objects.create(
            unit=unit,
            applicant=tenant,
            expires_at=timezone.now() + timedelta(days=2),
            listing_enabled_before_reservation=True,
            created_by=user,
        )
        unit.listing_enabled = False
        unit.save(update_fields=["listing_enabled"])

        created = self.client.post(
            "/api/v1/tenancies/create",
            data={
                "unit_id": unit.pk,
                "tenant_id": tenant.pk,
                "tenancy_type": Tenancy.TYPE_FIXED_TERM,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "due_day": 5,
                "grace_period_days": 3,
                "notice_period_days": 60,
                "rent_escalation_terms": {
                    "frequency": "annual",
                    "percentage": "5",
                },
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        tenancy = Tenancy.objects.get(unit=unit)
        reservation.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(tenancy.due_day, 5)
        self.assertEqual(reservation.status, UnitReservation.STATUS_RELEASED)
        self.assertEqual(unit.status, Unit.STATUS_BOOKED)
        self.assertFalse(unit.listing_enabled)
        self.assertTrue(
            AuditLog.objects.filter(
                action="tenancy.created", entity="Tenancy", user=user
            ).exists()
        )

        overlap = self.client.post(
            "/api/v1/tenancies/create",
            data={
                "unit_id": unit.pk,
                "tenant_id": tenant.pk,
                "tenancy_type": Tenancy.TYPE_FIXED_TERM,
                "start_date": "2026-06-01",
                "end_date": "2027-01-01",
                "due_day": 5,
            },
            content_type="application/json",
        )
        self.assertEqual(overlap.status_code, 409)

    def test_move_in_occupancy_requires_confirmed_payment_and_start_date(self):
        manager = self.create_user(phone="254712345684")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        self.client.force_login(manager)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Occupancy Transition Property",
            property_type="Apartment",
            location="Kilimani",
        )

        def create_move_in(suffix, start_date):
            unit = Unit.objects.create(
                property=property_obj,
                code=f"OT-{suffix}",
                unit_type="Studio",
                status=Unit.STATUS_BOOKED,
            )
            tenant = Tenant.objects.create(
                branch=self.nairobi,
                tenant_type=Tenant.INDIVIDUAL,
                name=f"Occupancy Tenant {suffix}",
                national_id_or_passport=f"ID-OCCUPANCY-{suffix}",
                phone=f"2547123456{suffix[-2:]}",
            )
            tenancy = Tenancy.objects.create(
                unit=unit,
                tenant=tenant,
                tenancy_type=Tenancy.TYPE_FIXED_TERM,
                start_date=start_date,
                end_date=date(2099, 12, 31),
                due_day=5,
            )
            package = MoveInPackage.objects.create(
                tenancy=tenancy,
                agreement_reference=f"MOVE-IN-OCCUPANCY-{suffix}",
            )
            charge = MoveInCharge.objects.create(
                package=package,
                charge_type=MoveInCharge.CHARGE_FEE,
                description="Move-in charge",
                amount=Decimal("100.00"),
            )
            invoice = Invoice.objects.create(
                branch=self.nairobi,
                tenancy=tenancy,
                number=f"INV-MOVE-IN-OCCUPANCY-{suffix}",
                period_start=start_date,
                period_end=start_date,
                due_date=start_date,
                total=Decimal("100.00"),
            )
            InvoiceLine.objects.create(
                invoice=invoice,
                charge_type=MoveInCharge.CHARGE_FEE,
                description=charge.description,
                amount=charge.amount,
                metadata={"move_in_charge_id": charge.pk},
            )
            return unit, tenant, tenancy, invoice

        unit, tenant, tenancy, invoice = create_move_in("001", date(2000, 3, 1))
        payment_details = {
            "tenant": tenant,
            "received_at": timezone.now(),
            "status": Payment.STATUS_ALLOCATED,
        }
        cash_payment = Payment.objects.create(
            **payment_details,
            amount=Decimal("40.00"),
            reference="MOVE-IN-OCCUPANCY-CASH",
            source=Payment.SOURCE_CASH,
            allocated_amount=Decimal("40.00"),
        )
        pending_payment = Payment.objects.create(
            **payment_details,
            amount=Decimal("50.00"),
            reference="MOVE-IN-OCCUPANCY-PENDING",
            source=Payment.SOURCE_CHEQUE,
            instrument_status=Payment.INSTRUMENT_PENDING,
            allocated_amount=Decimal("50.00"),
        )
        bounced_payment = Payment.objects.create(
            **payment_details,
            amount=Decimal("10.00"),
            reference="MOVE-IN-OCCUPANCY-BOUNCED",
            source=Payment.SOURCE_CHEQUE,
            instrument_status=Payment.INSTRUMENT_BOUNCED,
            allocated_amount=Decimal("10.00"),
        )
        for payment, amount in (
            (cash_payment, "40.00"),
            (pending_payment, "50.00"),
            (bounced_payment, "10.00"),
        ):
            PaymentAllocation.objects.create(
                payment=payment,
                invoice=invoice,
                amount=Decimal(amount),
            )

        self.assertFalse(sync_move_in_occupancy(tenancy.pk, as_of=date(2050, 3, 1)))
        self.assertEqual(
            self.client.get(
                f"/api/v1/tenancies/{tenancy.pk}/move-in-status"
            ).json()["invoice"]["confirmed_paid"],
            "40.00",
        )
        pending_payment.instrument_status = Payment.INSTRUMENT_CLEARED
        pending_payment.save(update_fields=["instrument_status"])
        self.assertFalse(sync_move_in_occupancy(tenancy.pk, as_of=date(2050, 3, 1)))
        final_payment = Payment.objects.create(
            **payment_details,
            amount=Decimal("10.00"),
            reference="MOVE-IN-OCCUPANCY-FINAL",
            source=Payment.SOURCE_CASH,
            allocated_amount=Decimal("10.00"),
        )
        PaymentAllocation.objects.create(
            payment=final_payment,
            invoice=invoice,
            amount=Decimal("10.00"),
        )
        status = self.client.get(f"/api/v1/tenancies/{tenancy.pk}/move-in-status")
        self.assertEqual(status.status_code, 200, status.content)
        self.assertEqual(status.json()["unit_status"], Unit.STATUS_OCCUPIED)
        unit.refresh_from_db()
        self.assertEqual(unit.status, Unit.STATUS_OCCUPIED)

        future_unit, future_tenant, future_tenancy, future_invoice = create_move_in(
            "002", date(2050, 3, 10)
        )
        future_payment = Payment.objects.create(
            tenant=future_tenant,
            amount=Decimal("100.00"),
            reference="MOVE-IN-OCCUPANCY-FUTURE",
            source=Payment.SOURCE_CASH,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("100.00"),
        )
        PaymentAllocation.objects.create(
            payment=future_payment,
            invoice=future_invoice,
            amount=Decimal("100.00"),
        )
        self.assertFalse(
            sync_move_in_occupancy(
                future_tenancy.pk,
                as_of=date(2050, 3, 9),
            )
        )
        with patch(
            "users.management.commands.sync_move_in_occupancy.timezone.localdate",
            return_value=date(2050, 3, 10),
        ):
            first_run = StringIO()
            call_command("sync_move_in_occupancy", stdout=first_run)
            second_run = StringIO()
            call_command("sync_move_in_occupancy", stdout=second_run)
        future_unit.refresh_from_db()
        self.assertEqual(future_unit.status, Unit.STATUS_OCCUPIED)
        self.assertIn("Marked 1", first_run.getvalue())
        self.assertIn("Marked 0", second_run.getvalue())

    def test_tenancy_api_rejects_invalid_term_configuration(self):
        user = self.create_user(phone="254712345696")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Invalid Tenancy Property",
            property_type="Apartment",
            location="Runda",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="T-002",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Invalid Tenancy Tenant",
            national_id_or_passport="ID-TENANCY-2",
            phone="254712345697",
        )
        invalid = self.client.post(
            "/api/v1/tenancies/create",
            data={
                "unit_id": unit.pk,
                "tenant_id": tenant.pk,
                "tenancy_type": Tenancy.TYPE_FIXED_TERM,
                "start_date": "2026-12-31",
                "end_date": "2026-01-01",
                "due_day": 32,
            },
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(Tenancy.objects.filter(unit=unit).exists())

    def test_move_in_package_prorates_rent_includes_deposit_and_is_idempotent(self):
        user = self.create_user(phone="254712345698")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Move In Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="M-001",
            unit_type="One bedroom",
            status=Unit.STATUS_OCCUPIED,
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("31000.00"),
            effective_from=date(2026, 1, 1),
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_DEPOSIT,
            name="Security deposit",
            amount=Decimal("31000.00"),
            effective_from=date(2026, 1, 1),
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RECURRING,
            name="Service charge",
            amount=Decimal("1500.00"),
            effective_from=date(2026, 1, 1),
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Move In Tenant",
            national_id_or_passport="ID-MOVE-IN-1",
            phone="254712345699",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_FIXED_TERM,
            start_date=date(2026, 1, 16),
            end_date=date(2026, 12, 31),
            due_day=5,
        )

        created = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/move-in-package",
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        package = MoveInPackage.objects.get(tenancy=tenancy)
        charges = {
            charge.charge_type: charge.amount
            for charge in MoveInCharge.objects.filter(package=package)
        }
        self.assertEqual(charges[UnitCharge.CHARGE_RENT], Decimal("16000.00"))
        self.assertEqual(charges[UnitCharge.CHARGE_DEPOSIT], Decimal("31000.00"))
        self.assertEqual(charges[UnitCharge.CHARGE_RECURRING], Decimal("1500.00"))
        self.assertEqual(MoveInChecklistItem.objects.filter(package=package).count(), 5)

        duplicate = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/move-in-package",
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(MoveInPackage.objects.filter(tenancy=tenancy).count(), 1)

        pdf = self.client.get(f"/api/v1/tenancies/{tenancy.pk}/move-in-agreement.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-1.4"))
        self.assertTrue(
            AuditLog.objects.filter(
                action="move_in_package.created",
                entity="MoveInPackage",
                user=user,
            ).exists()
        )

    @patch("users.views._send_tenant_activation_email")
    def test_move_in_invoice_uses_package_charges_and_waits_for_confirmed_payment(
        self,
        send_email,
    ):
        user = self.create_user(phone="254712345681")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.groups.add(Group.objects.get(name="Branch Manager"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Move In Invoice Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="MI-INV-001",
            unit_type="One bedroom",
            status=Unit.STATUS_BOOKED,
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("31000.00"),
            effective_from=date(2026, 1, 1),
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_DEPOSIT,
            name="Security deposit",
            amount=Decimal("31000.00"),
            effective_from=date(2026, 1, 1),
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Move In Invoice Tenant",
            national_id_or_passport="ID-MOVE-IN-INVOICE-1",
            phone="254712345682",
            email="move-in-invoice@example.com",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_FIXED_TERM,
            start_date=date(2026, 1, 16),
            end_date=date(2026, 12, 31),
            due_day=5,
        )
        self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/move-in-package",
            content_type="application/json",
        )

        issued = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/move-in-invoice",
            content_type="application/json",
        )
        self.assertEqual(issued.status_code, 201, issued.content)
        invoice = Invoice.objects.get(tenancy=tenancy)
        self.assertEqual(invoice.period_start, tenancy.start_date)
        self.assertEqual(invoice.due_date, tenancy.start_date)
        self.assertEqual(invoice.total, Decimal("47000.00"))
        unit.refresh_from_db()
        self.assertEqual(unit.status, Unit.STATUS_BOOKED)
        self.assertEqual(
            set(invoice.lines.values_list("description", flat=True)),
            {"Monthly rent", "Security deposit"},
        )
        self.assertFalse(send_email.called)

        partial_payment = self.client.post(
            "/api/v1/payments/offline/create",
            data={
                "tenant_id": tenant.pk,
                "amount": "1000.00",
                "source": Payment.SOURCE_CASH,
                "reference": "MOVE-IN-CASH-001",
                "collected_by": user.pk,
            },
            content_type="application/json",
        )
        self.assertEqual(partial_payment.status_code, 201, partial_payment.content)
        self.assertFalse(send_email.called)

        cheque = self.client.post(
            "/api/v1/payments/offline/create",
            data={
                "tenant_id": tenant.pk,
                "amount": str(invoice.total - Decimal("1000.00")),
                "source": Payment.SOURCE_CHEQUE,
                "reference": "MOVE-IN-CHEQUE-001",
                "cheque_number": "CHEQUE-001",
            },
            content_type="application/json",
        )
        self.assertEqual(cheque.status_code, 201, cheque.content)
        self.assertFalse(send_email.called)
        payment_id = cheque.json()["payment"]["id"]
        cleared = self.client.post(
            f"/api/v1/payments/{payment_id}/cheque-status",
            data={"status": Payment.INSTRUMENT_CLEARED},
            content_type="application/json",
        )
        self.assertEqual(cleared.status_code, 200, cleared.content)
        unit.refresh_from_db()
        self.assertEqual(unit.status, Unit.STATUS_OCCUPIED)
        send_email.assert_called_once()
        activation_url = send_email.call_args.args[-2]
        activation_code = send_email.call_args.args[-1]
        token = activation_url.split("token=", 1)[1]
        completed = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={
                "token": token,
                "code": activation_code,
                "password": "Tenant-secure-password-91!",
            },
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        self.assertTrue(TenantActivationChallenge.objects.get(tenant=tenant).email_sent_at)

        duplicate = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/move-in-invoice",
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Invoice.objects.filter(tenancy=tenancy).count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                action="invoice.move_in_issued",
                entity="Invoice",
                user=user,
            ).exists()
        )

    def test_tenant_activation_token_is_expiring_single_use_and_creates_portal_user(self):
        user = self.create_user(phone="254712345680")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Activation Property",
            property_type="Apartment",
            location="Kilimani",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="A-100",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Activation Tenant",
            national_id_or_passport="ID-ACT-1",
            phone="254712345681",
            email="activation@example.com",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=5,
        )
        issued = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/tenant-activation",
            content_type="application/json",
        )
        self.assertEqual(issued.status_code, 201)
        token = issued.json()["activation_token"]
        activation_code = issued.json()["activation_code"]
        challenge = TenantActivationChallenge.objects.get(tenant=tenant)
        self.assertNotEqual(challenge.token_hash, token)
        self.assertNotEqual(challenge.code_hash, activation_code)

        Group.objects.filter(name=TENANT_PORTAL_ROLE).delete()
        activated = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={"token": token, "code": activation_code, "password": "a-secure-password"},
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200)
        tenant.refresh_from_db()
        self.assertIsNotNone(tenant.portal_user_id)
        self.assertTrue(tenant.portal_user.is_active)
        self.assertTrue(tenant.portal_user.groups.filter(name=TENANT_PORTAL_ROLE).exists())
        self.assertTrue(tenant.portal_user.check_password("a-secure-password"))
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["user"]["id"], user.pk)
        self.assertEqual(
            self.client.post(
                "/api/v1/tenant-activation/complete",
                data={"token": token, "code": activation_code, "password": "another-password"},
                content_type="application/json",
            ).status_code,
            400,
        )

    def test_tenant_activation_code_requires_matching_active_unit_without_auto_login(self):
        manager = self.create_user(phone="254712345682")
        manager.groups.add(Group.objects.get(name="Property Officer / Agent"))
        manager.profile.branches.add(self.nairobi)
        self.client.force_login(manager)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Code Activation Property",
            property_type="Apartment",
            location="Westlands",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="B-201",
            unit_type="One bedroom",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Code Activation Tenant",
            national_id_or_passport="ID-CODE-1",
            phone="254712345683",
            email="code-activation@example.com",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=date(2026, 1, 1),
            due_day=5,
        )
        issued = self.client.post(
            f"/api/v1/tenancies/{tenancy.pk}/tenant-activation",
            content_type="application/json",
        )
        code = issued.json()["activation_code"]
        self.assertEqual(
            self.client.post(
                "/api/v1/tenant-activation/verify",
                data={"unit_code": "wrong-unit", "code": code},
                content_type="application/json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/tenant-activation/verify",
                data={"unit_code": "b-201", "code": code},
                content_type="application/json",
            ).json()["tenant_id"],
            tenant.pk,
        )

        self.client.logout()
        completed = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={
                "code": code,
                "unit_code": unit.code,
                "password": "Tenant-secure-password-91!",
                "full_name": "Tenant Updated Name",
            },
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200)
        tenant.refresh_from_db()
        self.assertTrue(tenant.portal_user.check_password("Tenant-secure-password-91!"))
        self.assertEqual(tenant.portal_user.profile.name, "Tenant Updated Name")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_tenant_api_supports_individuals_companies_and_kyc_documents(self):
        user = self.create_user(phone="254712345690")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)

        individual = self.client.post(
            "/api/v1/tenants/create",
            data={
                "branch_id": self.nairobi.pk,
                "tenant_type": Tenant.INDIVIDUAL,
                "name": "Amina Otieno",
                "national_id_or_passport": "ID-12345",
                "kra_pin": "A012345678B",
                "phone": "254712345691",
                "email": "amina@example.com",
            },
            content_type="application/json",
        )
        self.assertEqual(individual.status_code, 201)
        tenant = Tenant.objects.get(name="Amina Otieno")
        self.assertEqual(individual.json()["tenant"]["kra_pin"], "A012345678B")

        company = self.client.post(
            "/api/v1/tenants/create",
            data={
                "branch_id": self.nairobi.pk,
                "tenant_type": Tenant.COMPANY,
                "name": "Acme Kenya Limited",
                "company_registration_number": "CPR-001",
                "contact_person": "John Kamau",
                "phone": "254712345692",
            },
            content_type="application/json",
        )
        self.assertEqual(company.status_code, 201)
        self.assertEqual(Tenant.objects.filter(tenant_type=Tenant.COMPANY).count(), 1)

        with patch("users.views.upload_private_object") as upload, patch(
            "users.views.read_private_object", return_value=b"%PDF-1.7 private document"
        ) as read:
            document = self.client.post(
                f"/api/v1/tenants/{tenant.pk}/documents",
                data={
                    "document_type": "identity_document",
                    "name": "Amina ID",
                    "file": SimpleUploadedFile(
                        "amina-id.pdf",
                        b"%PDF-1.7 private document",
                        content_type="application/pdf",
                    ),
                },
            )
            self.assertEqual(document.status_code, 201, document.content)
            self.assertNotIn("storage_key", document.json()["document"])
            document_record = TenantDocument.objects.get(tenant=tenant)
            self.assertTrue(
                document_record.storage_key.startswith(f"tenant-documents/{tenant.pk}/")
            )
            upload.assert_called_once()
            downloaded = self.client.get(
                f"/api/v1/tenants/{tenant.pk}/documents/{document_record.pk}"
            )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"%PDF-1.7 private document")
        self.assertEqual(downloaded["Cache-Control"], "private, no-store")
        read.assert_called_once_with(document_record.storage_key)
        self.assertEqual(document.status_code, 201)
        self.assertTrue(TenantDocument.objects.filter(tenant=tenant).exists())
        audit = AuditLog.objects.get(
            action="tenant.created",
            entity="Tenant",
            user=user,
            new_values__id=tenant.pk,
        )
        self.assertEqual(audit.new_values["kra_pin"], "[redacted]")
        self.assertTrue(
            AuditLog.objects.filter(
                action="tenant_document.created",
                entity="TenantDocument",
                user=user,
            ).exists()
        )

    def test_tenant_api_enforces_scope_and_blacklist_reason(self):
        user = self.create_user(phone="254712345693")
        user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)

        invalid = self.client.post(
            "/api/v1/tenants/create",
            data={
                "branch_id": self.nairobi.pk,
                "tenant_type": Tenant.INDIVIDUAL,
                "name": "Missing ID",
                "phone": "254712345694",
            },
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(Tenant.objects.filter(name="Missing ID").exists())

        blacklisted = self.client.post(
            "/api/v1/tenants/create",
            data={
                "branch_id": self.nairobi.pk,
                "tenant_type": Tenant.INDIVIDUAL,
                "name": "Blacklisted Tenant",
                "national_id_or_passport": "ID-BLACKLISTED",
                "phone": "254712345695",
                "is_blacklisted": True,
            },
            content_type="application/json",
        )
        self.assertEqual(blacklisted.status_code, 400)

        hidden = Tenant.objects.create(
            branch=self.nakuru,
            tenant_type=Tenant.INDIVIDUAL,
            name="Nakuru Tenant",
            national_id_or_passport="ID-NAKURU-TENANT",
            phone="254712345696",
        )
        self.assertEqual(self.client.get(f"/api/v1/tenants/{hidden.pk}").status_code, 404)

    def test_super_admin_can_view_audit_log_without_mutation_permissions(self):
        user = self.create_user()
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        user.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        request = RequestFactory().get("/admin/users/auditlog/")
        request.user = user
        audit_admin = AuditLogAdmin(AuditLog, admin.site)

        self.assertTrue(audit_admin.has_module_permission(request))
        self.assertTrue(audit_admin.has_view_permission(request))
        self.assertFalse(audit_admin.has_add_permission(request))
        self.assertFalse(audit_admin.has_change_permission(request))
        self.assertFalse(audit_admin.has_delete_permission(request))

    def test_finance_role_django_admin_login_requires_sms_verification(self):
        user = self.create_user()
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        user.groups.add(Group.objects.get(name="Accountant"))
        delivered = []
        client = Client()

        failed_password = client.post(
            "/admin/login/",
            {"username": user.email, "password": "wrong"},
        )
        self.assertEqual(failed_password.status_code, 200)
        self.assertIsNone(client.session.get("_auth_user_id"))
        self.assertTrue(
            AuditLog.objects.filter(action="login.failed", user__isnull=True).exists()
        )

        with patch(
            "users.admin_authentication.queue_template_sms",
            side_effect=lambda phone, template_name, context, **kwargs: delivered.append(
                render_sms_template(template_name, context)
            ),
        ):
            challenged = client.post(
                "/admin/login/",
                {"username": user.email, "password": "Long-test-passphrase-29!"},
            )

        self.assertEqual(challenged.status_code, 200)
        self.assertIsNone(client.session.get("_auth_user_id"))
        self.assertEqual(SMSLoginChallenge.objects.filter(user=user).count(), 1)

        code = re.search(r"\b(\d{6})\b", delivered[0]).group(1)
        wrong_code = "000000" if code != "000000" else "000001"
        rejected = client.post(
            "/admin/login/",
            {
                "username": user.email,
                "password": "Long-test-passphrase-29!",
                "mfa_code": wrong_code,
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(
            SMSLoginChallenge.objects.get(user=user).attempts,
            1,
        )

        verified = client.post(
            "/admin/login/",
            {
                "username": user.email,
                "password": "Long-test-passphrase-29!",
                "mfa_code": code,
            },
        )

        self.assertEqual(verified.status_code, 302)
        self.assertEqual(client.session.get("_auth_user_id"), str(user.pk))
        self.assertFalse(SMSLoginChallenge.objects.filter(user=user).exists())
        self.assertTrue(
            AuditLog.objects.filter(user=user, action="login.succeeded").exists()
        )

    def test_non_finance_django_admin_login_is_audited(self):
        user = self.create_user()
        user.is_staff = True
        user.save(update_fields=["is_staff"])

        response = self.client.post(
            "/admin/login/",
            {"username": user.email, "password": "Long-test-passphrase-29!"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.succeeded",
            ).exists()
        )

    def test_email_and_phone_login_establish_a_session(self):
        user = self.create_user()

        for identifier in (user.email, user.profile.phone):
            self.client.post(
                "/api/v1/auth/logout",
                data="{}",
                content_type="application/json",
            )
            response = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": identifier, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["id"], user.pk)
            self.assertEqual(
                self.client.get("/api/v1/auth/me").json()["user"]["id"],
                user.pk,
            )

    def test_auth_session_uses_configured_lifetime_and_expires(self):
        user = self.create_user()
        response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            int(response.cookies[settings.SESSION_COOKIE_NAME]["max-age"]),
            settings.SESSION_COOKIE_AGE,
        )
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

        session = self.client.session
        session.set_expiry(-1)
        session.save()

        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_logout_ends_the_authenticated_session(self):
        user = self.create_user()
        login_response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

        logout_response = self.client.post(
            "/api/v1/auth/logout",
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(logout_response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_tenant_dashboard_returns_only_linked_tenant_billing_and_receipts(self):
        portal_user = self.create_user(
            email="tenant-dashboard@example.com",
            phone="254712345681",
        )
        portal_user.groups.add(Group.objects.get(name="Tenant (portal)"))
        other_user = self.create_user(
            email="other-dashboard-tenant@example.com",
            phone="254712345682",
        )
        other_user.groups.add(Group.objects.get(name="Tenant (portal)"))
        _, property_obj = self.create_portal_landlord(email="tenant-dashboard-owner@example.com")
        unit = Unit.objects.create(
            property=property_obj,
            code="TENANT-DASH-1",
            unit_type="One bedroom",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Dashboard Tenant",
            national_id_or_passport="TENANT-DASHBOARD-1",
            phone="254712345681",
            email=portal_user.email,
            portal_user=portal_user,
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=timezone.localdate() - timedelta(days=60),
            due_day=1,
        )
        deposit_account = DepositAccount.objects.create(
            tenancy=tenancy,
            target_amount=Decimal("20000.00"),
            balance=Decimal("15000.00"),
        )
        DepositEntry.objects.create(
            account=deposit_account,
            entry_type=DepositEntry.TYPE_RECEIPT,
            amount=Decimal("15000.00"),
            reference="TENANT-DASH-DEPOSIT-1",
            description="Security deposit received",
            created_by=portal_user,
        )
        today = timezone.localdate()
        current_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-TENANT-DASH-CURRENT",
            period_start=today.replace(day=1),
            period_end=today,
            due_date=today + timedelta(days=5),
            total=Decimal("12000.00"),
        )
        InvoiceLine.objects.create(
            invoice=current_invoice,
            charge_type=UnitCharge.CHARGE_RENT,
            description="Monthly rent",
            amount=Decimal("10000.00"),
        )
        InvoiceLine.objects.create(
            invoice=current_invoice,
            charge_type="utility",
            description="Water",
            amount=Decimal("2000.00"),
        )
        prior_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-TENANT-DASH-PRIOR",
            period_start=today.replace(day=1) - timedelta(days=1),
            period_end=today.replace(day=1) - timedelta(days=1),
            due_date=today - timedelta(days=10),
            total=Decimal("5000.00"),
        )
        current_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("8000.00"),
            reference="TENANT-DASH-PAYMENT-1",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("8000.00"),
        )
        PaymentAllocation.objects.create(
            payment=current_payment,
            invoice=current_invoice,
            amount=Decimal("8000.00"),
        )
        Receipt.objects.create(
            payment=current_payment,
            number="RCT-TENANT-DASH-1",
            tenant=tenant,
            amount=Decimal("8000.00"),
            balance_after=Decimal("9000.00"),
        )
        bounced_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("1000.00"),
            reference="TENANT-DASH-BOUNCED",
            source=Payment.SOURCE_CHEQUE,
            instrument_status=Payment.INSTRUMENT_BOUNCED,
            received_at=timezone.now(),
        )
        PaymentAllocation.objects.create(
            payment=bounced_payment,
            invoice=current_invoice,
            amount=Decimal("1000.00"),
        )
        prior_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("1000.00"),
            reference="TENANT-DASH-PAYMENT-2",
            source=Payment.SOURCE_MANUAL,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("1000.00"),
        )
        PaymentAllocation.objects.create(
            payment=prior_payment,
            invoice=prior_invoice,
            amount=Decimal("1000.00"),
        )
        ArrearsWaiver.objects.create(
            tenancy=tenancy,
            invoice=prior_invoice,
            amount=Decimal("500.00"),
            reason="Approved tenant dashboard test waiver",
            approved_by=portal_user,
        )

        other_tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Other Dashboard Tenant",
            national_id_or_passport="TENANT-DASHBOARD-OTHER",
            phone="254712345682",
            portal_user=other_user,
        )
        other_property = Property.objects.create(
            branch=self.nairobi,
            name="Other Tenant Dashboard Property",
            property_type="residential",
            location="Nairobi",
        )
        other_unit = Unit.objects.create(
            property=other_property,
            code="TENANT-DASH-OTHER",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        other_tenancy = Tenancy.objects.create(
            unit=other_unit,
            tenant=other_tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=today - timedelta(days=30),
        )
        other_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=other_tenancy,
            number="INV-TENANT-DASH-OTHER",
            period_start=today.replace(day=1),
            period_end=today,
            due_date=today,
            total=Decimal("99000.00"),
        )
        other_payment = Payment.objects.create(
            tenant=other_tenant,
            amount=Decimal("99000.00"),
            reference="TENANT-DASH-OTHER-PAYMENT",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
        )
        Receipt.objects.create(
            payment=other_payment,
            number="RCT-TENANT-DASH-OTHER",
            tenant=other_tenant,
            amount=Decimal("99000.00"),
            balance_after=Decimal("0.00"),
        )

        self.client.force_login(portal_user)
        response = self.client.get("/api/v1/tenant/dashboard")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tenant"]["unit_code"], "TENANT-DASH-1")
        self.assertEqual(payload["tenant"]["property_name"], property_obj.name)
        self.assertEqual(payload["tenant"]["notice_period_days"], tenancy.notice_period_days)
        self.assertEqual(payload["deposit"]["balance"], "15000.00")
        self.assertEqual(
            payload["deposit"]["entries"][0]["reference"],
            "TENANT-DASH-DEPOSIT-1",
        )
        self.assertEqual(payload["summary"]["balance_due"], "7500.00")
        self.assertEqual(payload["summary"]["next_due_date"], prior_invoice.due_date.isoformat())
        self.assertEqual(
            {item["number"] for item in payload["invoices"]},
            {current_invoice.number, prior_invoice.number},
        )
        current_row = next(
            item for item in payload["invoices"] if item["id"] == current_invoice.pk
        )
        self.assertEqual(current_row["amount_paid"], "8000.00")
        self.assertEqual(current_row["balance_due"], "4000.00")
        self.assertEqual(len(current_row["lines"]), 2)
        prior_row = next(
            item for item in payload["invoices"] if item["id"] == prior_invoice.pk
        )
        self.assertEqual(prior_row["waived_amount"], "500.00")
        self.assertEqual(
            prior_row["approved_waivers"][0]["reason"],
            "Approved tenant dashboard test waiver",
        )
        payment_with_receipt = next(
            item for item in payload["payments"] if item["id"] == current_payment.pk
        )
        self.assertEqual(payment_with_receipt["receipt"]["number"], "RCT-TENANT-DASH-1")
        self.assertNotIn(
            other_invoice.number,
            {item["number"] for item in payload["invoices"]},
        )
        self.assertNotIn(other_payment.pk, {item["id"] for item in payload["payments"]})
        self.assertTrue(
            AuditLog.objects.filter(
                user=portal_user,
                action="tenant_dashboard.viewed",
                entity="TenantDashboard",
                new_values__tenant_id=tenant.pk,
            ).exists()
        )

    def test_tenant_dashboard_requires_a_linked_tenant_portal_account(self):
        self.assertEqual(self.client.get("/api/v1/tenant/dashboard").status_code, 401)

        non_tenant = self.create_user(
            email="not-a-tenant-dashboard@example.com",
            phone="254712345683",
        )
        self.client.force_login(non_tenant)
        self.assertEqual(self.client.get("/api/v1/tenant/dashboard").status_code, 403)

        unlinked_tenant_user = self.create_user(
            email="unlinked-tenant-dashboard@example.com",
            phone="254712345684",
        )
        unlinked_tenant_user.groups.add(Group.objects.get(name="Tenant (portal)"))
        self.client.force_login(unlinked_tenant_user)
        self.assertEqual(self.client.get("/api/v1/tenant/dashboard").status_code, 404)

    def test_landlord_dashboard_aggregates_only_owned_property_data(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="dashboard-landlord@example.com"
        )
        self.client.force_login(landlord_user)
        today = timezone.localdate()
        period_start = today.replace(day=1)
        period_end = (
            date(today.year + 1, 1, 1) - timedelta(days=1)
            if today.month == 12
            else date(today.year, today.month + 1, 1) - timedelta(days=1)
        )
        occupied_unit = Unit.objects.create(
            property=property_obj,
            code="DASH-OCC",
            unit_type="One bedroom",
            status=Unit.STATUS_OCCUPIED,
        )
        Unit.objects.create(
            property=property_obj,
            code="DASH-VAC",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
        )
        Unit.objects.create(
            property=property_obj,
            code="DASH-ARCH",
            unit_type="Studio",
            status=Unit.STATUS_ARCHIVED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Dashboard Tenant",
            national_id_or_passport="ID-DASHBOARD-1",
            phone="254712345677",
        )
        tenancy = Tenancy.objects.create(
            unit=occupied_unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=period_start,
            due_day=1,
        )
        current_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-DASH-CURRENT",
            period_start=period_start,
            period_end=period_end,
            due_date=today,
            total=Decimal("20000.00"),
        )
        collected_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("8000.00"),
            reference="DASH-COLLECTED",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("8000.00"),
        )
        PaymentAllocation.objects.create(
            payment=collected_payment,
            invoice=current_invoice,
            amount=Decimal("8000.00"),
        )
        bank_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("1500.00"),
            reference="DASH-BANK-COLLECTED",
            source=Payment.SOURCE_BANK,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("1500.00"),
        )
        PaymentAllocation.objects.create(
            payment=bank_payment,
            invoice=current_invoice,
            amount=Decimal("1500.00"),
        )
        pending_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("500.00"),
            reference="DASH-MPESA-PENDING",
            source=Payment.SOURCE_MPESA,
            instrument_status=Payment.INSTRUMENT_PENDING,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("500.00"),
        )
        PaymentAllocation.objects.create(
            payment=pending_payment,
            invoice=current_invoice,
            amount=Decimal("500.00"),
        )
        bounced_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("2000.00"),
            reference="DASH-BOUNCED",
            source=Payment.SOURCE_CHEQUE,
            instrument_status=Payment.INSTRUMENT_BOUNCED,
            received_at=timezone.now(),
        )
        PaymentAllocation.objects.create(
            payment=bounced_payment,
            invoice=current_invoice,
            amount=Decimal("2000.00"),
        )
        overdue_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-DASH-OVERDUE",
            period_start=period_start - timedelta(days=31),
            period_end=period_start - timedelta(days=1),
            due_date=today - timedelta(days=1),
            total=Decimal("5000.00"),
        )
        overdue_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("1000.00"),
            reference="DASH-OVERDUE-PAYMENT",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("1000.00"),
        )
        PaymentAllocation.objects.create(
            payment=overdue_payment,
            invoice=overdue_invoice,
            amount=Decimal("1000.00"),
        )
        ArrearsWaiver.objects.create(
            tenancy=tenancy,
            invoice=overdue_invoice,
            amount=Decimal("500.00"),
            reason="Approved dashboard test waiver",
            approved_by=landlord_user,
        )
        PropertyExpense.objects.create(
            property=property_obj,
            category="Repairs",
            description="Approved current-month repair",
            amount=Decimal("1500.00"),
            effective_from=period_start,
            submitted_by=landlord_user,
            approved_by=landlord_user,
            approved_at=timezone.now(),
            status=PropertyExpense.STATUS_APPROVED,
        )
        PropertyExpense.objects.create(
            property=property_obj,
            category="Cleaning",
            description="Pending current-month expense",
            amount=Decimal("600.00"),
            effective_from=period_start,
            submitted_by=landlord_user,
        )
        PropertyExpense.objects.create(
            property=property_obj,
            category="Repairs",
            description="Rejected current-month expense",
            amount=Decimal("300.00"),
            effective_from=period_start,
            submitted_by=landlord_user,
            status=PropertyExpense.STATUS_REJECTED,
            rejection_reason="Not approved",
        )
        MaintenanceTicket.objects.create(
            property=property_obj,
            reported_by=landlord_user,
            title="Open dashboard ticket",
            description="A ticket that should be counted.",
            category=MaintenanceTicket.CATEGORY_PLUMBING,
        )
        MaintenanceTicket.objects.create(
            property=property_obj,
            reported_by=landlord_user,
            title="Completed dashboard ticket",
            description="A completed ticket should not be counted.",
            category=MaintenanceTicket.CATEGORY_PLUMBING,
            status=MaintenanceTicket.STATUS_COMPLETED,
            completed_at=timezone.now(),
        )

        other_landlord, other_property = self.create_portal_landlord(
            email="other-dashboard-landlord@example.com"
        )
        other_unit = Unit.objects.create(
            property=other_property,
            code="OTHER-DASH",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        other_tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Other Dashboard Tenant",
            national_id_or_passport="ID-DASHBOARD-OTHER",
            phone="254712345676",
        )
        other_tenancy = Tenancy.objects.create(
            unit=other_unit,
            tenant=other_tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=period_start,
            due_day=1,
        )
        Invoice.objects.create(
            branch=self.nairobi,
            tenancy=other_tenancy,
            number="INV-DASH-OTHER",
            period_start=period_start,
            period_end=period_end,
            due_date=today,
            total=Decimal("99000.00"),
        )
        other_invoice = Invoice.objects.get(number="INV-DASH-OTHER")
        other_payment = Payment.objects.create(
            tenant=other_tenant,
            amount=Decimal("99000.00"),
            reference="DASH-OTHER-LANDLORD",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("99000.00"),
        )
        PaymentAllocation.objects.create(
            payment=other_payment,
            invoice=other_invoice,
            amount=Decimal("99000.00"),
        )

        response = self.client.get("/api/v1/landlord/dashboard")

        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertEqual(report["period_start"], period_start.isoformat())
        self.assertEqual(report["period_end"], period_end.isoformat())
        self.assertEqual(len(report["properties"]), 1)
        self.assertEqual(report["properties"][0]["id"], property_obj.pk)
        self.assertEqual(report["summary"]["total_units"], 2)
        self.assertEqual(report["summary"]["occupied_units"], 1)
        self.assertEqual(report["summary"]["occupancy_rate"], 50)
        self.assertEqual(report["summary"]["expected_rent"], "20000.00")
        self.assertEqual(report["summary"]["collected_rent"], "9500.00")
        self.assertIsNone(report["summary"]["projected_net_payout"])
        self.assertEqual(
            {item["reference"] for item in report["collections"]},
            {
                "DASH-COLLECTED",
                "DASH-BANK-COLLECTED",
                "DASH-OVERDUE-PAYMENT",
            },
        )
        self.assertNotIn(
            "DASH-MPESA-PENDING",
            {item["reference"] for item in report["collections"]},
        )
        self.assertNotIn(
            "DASH-OTHER-LANDLORD",
            {item["reference"] for item in report["collections"]},
        )
        self.assertEqual(report["summary"]["arrears"], "3500.00")
        self.assertEqual(report["summary"]["approved_expenses"], "1500.00")
        self.assertEqual(report["summary"]["pending_expenses"], "600.00")
        self.assertEqual(report["summary"]["pending_expense_count"], 1)
        self.assertEqual(report["summary"]["open_maintenance_count"], 1)
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="landlord_dashboard.viewed",
                entity="LandlordDashboard",
                new_values__property_count=1,
            ).exists()
        )

    def test_landlord_dashboard_alerts_only_for_submitted_quotes_above_threshold(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="dashboard-quote-landlord@example.com"
        )
        self.client.force_login(landlord_user)
        today = timezone.localdate()
        ManagementAgreement.objects.create(
            landlord=property_obj.landlord,
            effective_from=today.replace(day=1),
            commission_model="percentage",
            commission_rate=Decimal("10.00"),
            terms={"maintenance_approval_threshold": "10000.00"},
        )
        ticket = MaintenanceTicket.objects.create(
            property=property_obj,
            reported_by=landlord_user,
            title="Roof repair",
            description="Repair a leak on the roof.",
            category=MaintenanceTicket.CATEGORY_STRUCTURAL,
        )
        vendor = Vendor.objects.create(
            branch=property_obj.branch,
            name="Roofing Specialist",
            trade="Roofing",
            phone="254712345697",
        )
        high_quote = MaintenanceQuotation.objects.create(
            ticket=ticket,
            vendor=vendor,
            description="Replace damaged roof sheets",
            amount=Decimal("25000.00"),
            status=MaintenanceQuotation.STATUS_SUBMITTED,
            submitted_by=landlord_user,
        )
        MaintenanceQuotation.objects.create(
            ticket=ticket,
            vendor=vendor,
            description="Temporary patch",
            amount=Decimal("5000.00"),
            status=MaintenanceQuotation.STATUS_SUBMITTED,
            submitted_by=landlord_user,
        )

        response = self.client.get("/api/v1/landlord/dashboard")

        self.assertEqual(response.status_code, 200)
        quotes = response.json()["pending_high_cost_repairs"]
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["id"], high_quote.pk)
        self.assertEqual(quotes[0]["approval_threshold"], "10000.00")

    def test_landlord_dashboard_requires_landlord_portal_role(self):
        self.assertEqual(
            self.client.get("/api/v1/landlord/dashboard").status_code,
            401,
        )
        user = self.create_user(email="not-a-portal-landlord@example.com")
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        self.assertEqual(
            self.client.get("/api/v1/landlord/dashboard").status_code,
            403,
        )

    def test_landlord_property_and_unit_lists_are_limited_to_all_owned_properties(self):
        landlord_user, first_property = self.create_portal_landlord(
            email="owned-list-landlord@example.com"
        )
        second_property = Property.objects.create(
            branch=self.nakuru,
            landlord=first_property.landlord,
            name="Second Owned List Property",
            property_type="Apartment",
            location="Nakuru",
        )
        first_unit = Unit.objects.create(
            property=first_property,
            code="OWNED-LIST-1",
            unit_type="Studio",
        )
        second_unit = Unit.objects.create(
            property=second_property,
            code="OWNED-LIST-2",
            unit_type="One bedroom",
        )
        other_landlord_user, other_property = self.create_portal_landlord(
            email="other-owned-list-landlord@example.com"
        )
        other_unit = Unit.objects.create(
            property=other_property,
            code="OTHER-OWNED-LIST-1",
            unit_type="Studio",
        )

        self.client.force_login(landlord_user)
        properties = self.client.get("/api/v1/landlord/properties")
        units = self.client.get("/api/v1/landlord/units")
        role_scoped_properties = self.client.get("/api/v1/properties")
        role_scoped_units = self.client.get("/api/v1/units")

        self.assertEqual(properties.status_code, 200)
        self.assertEqual(units.status_code, 200)
        self.assertEqual(role_scoped_properties.status_code, 200)
        self.assertEqual(role_scoped_units.status_code, 200)
        self.assertEqual(
            {item["id"] for item in properties.json()["properties"]},
            {first_property.pk, second_property.pk},
        )
        self.assertEqual(
            {item["id"] for item in role_scoped_properties.json()["properties"]},
            {first_property.pk, second_property.pk},
        )
        self.assertEqual(
            {item["id"] for item in units.json()["units"]},
            {first_unit.pk, second_unit.pk},
        )
        self.assertEqual(
            {item["id"] for item in role_scoped_units.json()["units"]},
            {first_unit.pk, second_unit.pk},
        )
        self.assertEqual(
            self.client.get(f"/api/v1/properties/{first_property.pk}").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/units/{first_unit.pk}").status_code,
            200,
        )
        self.assertNotIn(
            other_property.pk,
            {item["id"] for item in properties.json()["properties"]},
        )
        self.assertNotIn(
            other_unit.pk,
            {item["id"] for item in units.json()["units"]},
        )
        self.assertEqual(
            self.client.get(f"/api/v1/properties/{other_property.pk}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/units/{other_unit.pk}").status_code,
            404,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="landlord_properties.viewed",
                entity="LandlordPropertyList",
                new_values__property_count=2,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="landlord_units.viewed",
                entity="LandlordUnitList",
                new_values__unit_count=2,
            ).exists()
        )

        self.client.force_login(other_landlord_user)
        self.assertEqual(
            {
                item["id"]
                for item in self.client.get("/api/v1/landlord/properties").json()["properties"]
            },
            {other_property.pk},
        )

    def test_landlord_property_details_and_inspection_photos_are_owner_scoped(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="property-details-landlord@example.com"
        )
        caretaker = self.create_user(
            email="property-details-caretaker@example.com",
            phone="254712345699",
        )
        PropertyCaretakerAssignment.objects.create(
            property=property_obj,
            caretaker=caretaker,
            assigned_by=landlord_user,
        )
        today = timezone.localdate()
        ManagementAgreement.objects.create(
            landlord=property_obj.landlord,
            effective_from=today.replace(day=1),
            commission_model="percentage",
            commission_rate=Decimal("8.00"),
            payout_day=5,
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="DETAIL-1",
            unit_type="One bedroom",
            status=Unit.STATUS_OCCUPIED,
        )
        UnitCharge.objects.create(
            unit=unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("15000.00"),
            effective_from=today.replace(day=1),
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Property Detail Tenant",
            national_id_or_passport="ID-PROPERTY-DETAIL",
            phone="254712345698",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=today.replace(day=1),
            due_day=1,
        )
        invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-PROPERTY-DETAIL",
            period_start=today.replace(day=1),
            period_end=today,
            due_date=today,
            total=Decimal("1000.00"),
        )
        payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("200.00"),
            reference="PROPERTY-DETAIL-PAYMENT",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("200.00"),
        )
        PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("200.00"),
        )
        now = timezone.now()
        inspection = PropertyInspection.objects.create(
            property=property_obj,
            title="Monthly condition check",
            inspection_type="routine",
            scheduled_for=now,
            responsible_staff=landlord_user,
            created_by=landlord_user,
            status=PropertyInspection.STATUS_COMPLETED,
            checklist=[{"title": "Check plumbing", "completed": True}],
            photos=[
                {
                    "storage_key": f"inspections/{property_obj.pk}/condition.jpg",
                    "content_type": "image/jpeg",
                    "caption": "Kitchen sink",
                }
            ],
            findings="No leaks found.",
            recommendations="Continue monthly checks.",
            completed_at=now,
            shared_at=now,
        )

        self.client.force_login(landlord_user)
        properties = self.client.get("/api/v1/landlord/properties")
        self.assertEqual(properties.status_code, 200)
        property_data = next(
            item
            for item in properties.json()["properties"]
            if item["id"] == property_obj.pk
        )
        self.assertEqual(property_data["management_agreement"]["commission_rate"], "8.00")
        self.assertEqual(property_data["caretakers"][0]["phone"], "254712345699")
        self.assertEqual(property_data["units"][0]["monthly_rent"], "15000.00")
        self.assertEqual(property_data["units"][0]["tenant_name"], tenant.name)
        self.assertEqual(property_data["units"][0]["balance"], "800.00")
        self.assertEqual(
            property_data["inspection_reports"][0]["id"],
            inspection.pk,
        )
        self.assertNotIn(
            "storage_key",
            property_data["inspection_reports"][0]["photos"][0],
        )

        with patch("users.views.read_private_object", return_value=b"photo") as read_photo:
            photo = self.client.get(
                f"/api/v1/landlord/inspections/{inspection.pk}/photos/0"
            )
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo.content, b"photo")
        self.assertEqual(photo["Content-Type"], "image/jpeg")
        read_photo.assert_called_once_with(
            f"inspections/{property_obj.pk}/condition.jpg"
        )

        _, other_property = self.create_portal_landlord(
            email="other-property-details-landlord@example.com"
        )
        self.client.force_login(other_property.landlord.portal_user)
        self.assertEqual(
            self.client.get(
                f"/api/v1/landlord/inspections/{inspection.pk}/photos/0"
            ).status_code,
            404,
        )

    def test_landlord_property_and_unit_lists_require_portal_role(self):
        self.assertEqual(
            self.client.get("/api/v1/landlord/properties").status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/v1/landlord/units").status_code,
            401,
        )
        staff_user = self.create_user(
            email="staff-landlord-list-denial@example.com",
            phone="254712345635",
        )
        staff_user.groups.add(Group.objects.get(name="Property Officer / Agent"))
        staff_user.profile.branches.add(self.nairobi)
        self.client.force_login(staff_user)
        self.assertEqual(
            self.client.get("/api/v1/landlord/properties").status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/v1/landlord/units").status_code,
            403,
        )

    def test_landlord_can_select_only_an_owned_active_property(self):
        landlord_user, first_property = self.create_portal_landlord(
            email="active-property-landlord@example.com"
        )
        second_property = Property.objects.create(
            branch=self.nairobi,
            landlord=first_property.landlord,
            name="Second Active Property",
            property_type="residential",
            location="Nairobi",
        )
        recurring_charge = PropertyRecurringCharge.objects.create(
            property=first_property,
            name="Garbage",
            amount=Decimal("250.00"),
        )
        _, other_property = self.create_portal_landlord(
            email="other-active-property-landlord@example.com"
        )
        self.client.force_login(landlord_user)

        initial = self.client.get("/api/v1/landlord/properties")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["activePropertyId"], first_property.pk)
        self.assertEqual(
            initial.json()["properties"][0]["recurringCharges"],
            [{
                "id": recurring_charge.pk,
                "name": "Garbage",
                "amount": "250.00",
                "is_active": True,
            }],
        )
        self.assertEqual(len(initial.json()["properties"]), 2)

        selected = self.client.patch(
            "/api/v1/landlord/properties",
            data={"activePropertyId": second_property.pk},
            content_type="application/json",
        )
        self.assertEqual(selected.status_code, 200)
        landlord_user.profile.refresh_from_db()
        self.assertEqual(landlord_user.profile.active_property_id, second_property.pk)

        forbidden = self.client.patch(
            "/api/v1/landlord/properties",
            data={"activePropertyId": other_property.pk},
            content_type="application/json",
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_landlord_can_create_property_and_recurring_charges_in_postgres(self):
        landlord_user, initial_property = self.create_portal_landlord(
            email="property-create-landlord@example.com"
        )
        self.client.force_login(landlord_user)

        response = self.client.post(
            "/api/v1/landlord/properties",
            data={
                "name": "New Rental Property",
                "address": "12 Market Road",
                "currencyCode": "kes",
                "timezone": "Africa/Nairobi",
                "rentDueDay": 5,
                "billingMode": "fixed_utilities",
                "defaultRent": "15000.00",
                "securityDeposit": "30000.00",
                "waterDeposit": "500.00",
                "garbageCharge": "250.00",
                "waterRate": "125.50",
                "recurringCharges": [
                    {"name": "Security", "amount": "450.00"},
                    {"name": "Garbage", "amount": 250, "isActive": False},
                ],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        property_id = response.json()["property"]["id"]
        property_obj = Property.objects.get(pk=property_id)
        self.assertEqual(property_obj.landlord.portal_user, landlord_user)
        self.assertEqual(property_obj.branch, initial_property.branch)
        self.assertEqual(property_obj.location, "12 Market Road")
        self.assertEqual(property_obj.address, "12 Market Road")
        self.assertEqual(property_obj.currency_code, "KES")
        self.assertEqual(property_obj.rent_due_day, 5)
        self.assertEqual(property_obj.billing_mode, Property.BILLING_MODE_FIXED_UTILITIES)
        self.assertEqual(property_obj.default_rent, Decimal("15000.00"))
        self.assertEqual(property_obj.default_water_rate, Decimal("125.5000"))
        self.assertEqual(response.json()["activePropertyId"], property_id)
        self.assertEqual(
            {
                (charge.name, charge.amount, charge.is_active)
                for charge in property_obj.recurring_charges.all()
            },
            {
                ("Security", Decimal("450.00"), True),
                ("Garbage", Decimal("250.00"), False),
            },
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="landlord_property.created",
                entity="Property",
                new_values__property_id=property_id,
            ).exists()
        )

        listed = self.client.get("/api/v1/landlord/properties")
        self.assertEqual(
            {item["id"] for item in listed.json()["properties"]},
            {initial_property.pk, property_id},
        )
        self.assertEqual(
            len(next(item for item in listed.json()["properties"] if item["id"] == property_id)["recurringCharges"]),
            2,
        )

        selected = self.client.patch(
            "/api/v1/landlord/properties",
            data={"activePropertyId": initial_property.pk},
            content_type="application/json",
        )
        self.assertEqual(selected.status_code, 200)
        additional = self.client.post(
            "/api/v1/landlord/properties",
            data={"name": "Another Rental Property"},
            content_type="application/json",
        )
        self.assertEqual(additional.status_code, 201)
        self.assertEqual(additional.json()["activePropertyId"], initial_property.pk)

    def test_landlord_can_update_only_owned_property_and_replace_recurring_charges_atomically(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="property-update-landlord@example.com"
        )
        original_charge = PropertyRecurringCharge.objects.create(
            property=property_obj,
            name="Old Charge",
            amount=Decimal("100.00"),
        )
        _, other_property = self.create_portal_landlord(
            email="property-update-other@example.com"
        )
        self.client.force_login(landlord_user)

        saved = self.client.patch(
            "/api/v1/landlord/properties",
            data={
                "propertyId": str(property_obj.pk),
                "name": "Renamed Property",
                "rentDueDay": 15,
                "recurringCharges": [
                    {"name": "New Charge", "amount": "275.50"},
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 200)
        property_obj.refresh_from_db()
        self.assertEqual(property_obj.name, "Renamed Property")
        self.assertEqual(property_obj.rent_due_day, 15)
        self.assertEqual(property_obj.currency_code, "KES")
        self.assertEqual(
            list(
                property_obj.recurring_charges.values_list(
                    "name", "amount", "is_active"
                )
            ),
            [("New Charge", Decimal("275.50"), True)],
        )
        self.assertFalse(PropertyRecurringCharge.objects.filter(pk=original_charge.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="landlord_property.updated",
                entity="Property",
                new_values__property_id=property_obj.pk,
            ).exists()
        )

        invalid = self.client.patch(
            "/api/v1/landlord/properties",
            data={
                "propertyId": property_obj.pk,
                "name": "Should Roll Back",
                "recurringCharges": [
                    {"name": "Duplicate", "amount": "5.00"},
                    {"name": " duplicate ", "amount": "8.00"},
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        property_obj.refresh_from_db()
        self.assertEqual(property_obj.name, "Renamed Property")
        self.assertEqual(
            list(property_obj.recurring_charges.values_list("name", flat=True)),
            ["New Charge"],
        )

        forbidden = self.client.patch(
            "/api/v1/landlord/properties",
            data={"propertyId": other_property.pk, "name": "Not Owned"},
            content_type="application/json",
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_landlord_property_creation_rejects_invalid_values_without_partial_writes(self):
        landlord_user, _ = self.create_portal_landlord(
            email="property-invalid-landlord@example.com"
        )
        self.client.force_login(landlord_user)

        invalid = self.client.post(
            "/api/v1/landlord/properties",
            data={
                "name": "Invalid Property",
                "currencyCode": "US",
                "rentDueDay": 29,
                "defaultRent": "-1",
                "recurringCharges": [{"name": "Charge", "amount": "5.00"}],
            },
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(
            Property.objects.filter(name="Invalid Property").exists()
        )
        self.assertEqual(
            PropertyRecurringCharge.objects.filter(name="Charge").count(),
            0,
        )

        unauthenticated = Client().post(
            "/api/v1/landlord/properties",
            data={"name": "Unauthorized Property"},
            content_type="application/json",
        )
        self.assertEqual(unauthenticated.status_code, 401)

    def test_landlord_billing_settings_are_scoped_validated_and_audited(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="billing-settings-landlord@example.com"
        )
        self.client.force_login(landlord_user)

        initial = self.client.get("/api/v1/landlord/billing-settings")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(
            initial.json()["settings"],
            {
                "paybill_number": "",
                "account_number": "",
                "universal_security_deposit": "8000",
                "universal_water_deposit": "500",
                "first_online_billing_month": None,
            },
        )

        saved = self.client.patch(
            "/api/v1/landlord/billing-settings",
            data={"paybillNumber": "123456", "accountNumber": "PROPERTY-001"},
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 200)
        settings_record = PropertyBillingSettings.objects.get(property=property_obj)
        self.assertEqual(settings_record.paybill_number, "123456")
        self.assertEqual(settings_record.account_number, "PROPERTY-001")
        self.assertEqual(settings_record.updated_by, landlord_user)
        self.assertEqual(saved.json()["settings"]["universal_security_deposit"], "8000")
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="property_billing_settings.updated",
                entity="PropertyBillingSettings",
                new_values__property_id=property_obj.pk,
            ).exists()
        )

        invalid = self.client.patch(
            "/api/v1/landlord/billing-settings",
            data={"paybillNumber": "x" * 33, "accountNumber": ""},
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        settings_record.refresh_from_db()
        self.assertEqual(settings_record.paybill_number, "123456")

    def test_landlord_statement_calculates_scoped_totals_and_exports_csv(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="statement-landlord@example.com"
        )
        self.client.force_login(landlord_user)
        today = timezone.localdate()
        period_start = today.replace(day=1)
        ManagementAgreement.objects.create(
            landlord=property_obj.landlord,
            effective_from=period_start,
            commission_model="percentage",
            commission_rate=Decimal("10.00"),
            mri_enabled=True,
            terms={
                "landlord_statement_tax": {
                    "name": "MRI",
                    "rate": "5.00",
                    "basis": "collected_invoice_allocations",
                    "approved": True,
                }
            },
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="STATEMENT-1",
            unit_type="One bedroom",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Statement Tenant",
            national_id_or_passport="ID-STATEMENT-1",
            phone="254712345675",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=period_start,
            due_day=1,
        )
        DepositAccount.objects.create(
            tenancy=tenancy,
            target_amount=Decimal("15000.00"),
            balance=Decimal("12000.00"),
        )
        invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-STATEMENT-CURRENT",
            period_start=period_start,
            period_end=today,
            due_date=today + timedelta(days=1),
            total=Decimal("12000.00"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            charge_type=UnitCharge.CHARGE_RENT,
            description="Monthly rent",
            amount=Decimal("10000.00"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            charge_type="utility",
            description="Water",
            amount=Decimal("2000.00"),
        )
        payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("8000.00"),
            reference="STATEMENT-COLLECTED",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("8000.00"),
        )
        PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("8000.00"),
        )
        bounced_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("1000.00"),
            reference="STATEMENT-BOUNCED",
            source=Payment.SOURCE_CHEQUE,
            instrument_status=Payment.INSTRUMENT_BOUNCED,
            received_at=timezone.now(),
            status=Payment.STATUS_UNMATCHED,
        )
        PaymentAllocation.objects.create(
            payment=bounced_payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )
        overdue_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-STATEMENT-ARREARS",
            period_start=period_start - timedelta(days=31),
            period_end=period_start - timedelta(days=1),
            due_date=period_start - timedelta(days=1),
            total=Decimal("5000.00"),
        )
        prior_payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("1000.00"),
            reference="STATEMENT-ARREARS-PAYMENT",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("1000.00"),
        )
        PaymentAllocation.objects.create(
            payment=prior_payment,
            invoice=overdue_invoice,
            amount=Decimal("1000.00"),
        )
        ArrearsWaiver.objects.create(
            tenancy=tenancy,
            invoice=overdue_invoice,
            amount=Decimal("500.00"),
            reason="Approved statement test waiver",
            approved_by=landlord_user,
        )
        PropertyExpense.objects.create(
            property=property_obj,
            category="Repairs",
            description="Approved in-period repair",
            amount=Decimal("500.00"),
            effective_from=period_start,
            submitted_by=landlord_user,
            approved_by=landlord_user,
            approved_at=timezone.now(),
            status=PropertyExpense.STATUS_APPROVED,
        )
        PropertyExpense.objects.create(
            property=property_obj,
            category="Cleaning",
            description="Pending in-period expense",
            amount=Decimal("700.00"),
            effective_from=period_start,
            submitted_by=landlord_user,
        )

        other_landlord, other_property = self.create_portal_landlord(
            email="other-statement-landlord@example.com"
        )
        other_unit = Unit.objects.create(
            property=other_property,
            code="OTHER-STATEMENT",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        other_tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Other Statement Tenant",
            national_id_or_passport="ID-STATEMENT-OTHER",
            phone="254712345674",
        )
        other_tenancy = Tenancy.objects.create(
            unit=other_unit,
            tenant=other_tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=period_start,
            due_day=1,
        )
        other_invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=other_tenancy,
            number="INV-STATEMENT-OTHER",
            period_start=period_start,
            period_end=today,
            due_date=today,
            total=Decimal("99000.00"),
        )
        InvoiceLine.objects.create(
            invoice=other_invoice,
            charge_type=UnitCharge.CHARGE_RENT,
            description="Other landlord rent",
            amount=Decimal("99000.00"),
        )
        LandlordRemittance.objects.create(
            landlord=property_obj.landlord,
            period_start=period_start,
            period_end=today,
            revision=1,
            statement_snapshot={
                "summary": {
                    "collected_invoice_allocations": "9000.00",
                    "commission": "900.00",
                    "expenses": "500.00",
                    "tax": "450.00",
                    "net_payout": "7150.00",
                },
                "commission_status": "configured",
                "tax_status": "configured",
            },
            collected_amount=Decimal("9000.00"),
            commission_amount=Decimal("900.00"),
            expenses_amount=Decimal("500.00"),
            tax_amount=Decimal("450.00"),
            net_payout=Decimal("7150.00"),
            status=LandlordRemittance.STATUS_PAID,
            prepared_by=landlord_user,
            payout_reference="STATEMENT-PAID-REF",
            payout_date=today,
        )

        query = f"?from={period_start.isoformat()}&to={today.isoformat()}"
        response = self.client.get(f"/api/v1/landlord/statements{query}")

        self.assertEqual(response.status_code, 200)
        statement = response.json()
        self.assertEqual(statement["landlord"]["id"], property_obj.landlord_id)
        self.assertEqual(statement["summary"]["billed_rent"], "10000.00")
        self.assertEqual(statement["summary"]["collected_invoice_allocations"], "9000.00")
        self.assertEqual(statement["summary"]["arrears"], "3500.00")
        self.assertEqual(statement["summary"]["commission"], "900.00")
        self.assertEqual(statement["summary"]["expenses"], "500.00")
        self.assertEqual(statement["summary"]["tax"], "450.00")
        self.assertEqual(statement["summary"]["net_payout"], "7150.00")
        self.assertEqual(statement["trust_deposits_total"], "12000.00")
        self.assertEqual(len(statement["trust_deposits"]), 1)
        self.assertEqual(len(statement["remittances"]), 1)
        self.assertEqual(statement["remittances"][0]["status"], LandlordRemittance.STATUS_PAID)
        self.assertEqual(
            statement["remittances"][0]["payout_reference"],
            "STATEMENT-PAID-REF",
        )
        self.assertEqual(statement["commission_status"], "configured")
        self.assertEqual(statement["tax_status"], "configured")
        self.assertEqual(len(statement["properties"]), 1)

        property_filtered = self.client.get(
            f"/api/v1/landlord/statements{query}&property_id={property_obj.pk}"
        )
        self.assertEqual(property_filtered.status_code, 200)
        self.assertEqual(property_filtered.json()["property_id"], property_obj.pk)
        self.assertEqual(
            property_filtered.json()["trust_deposits_total"],
            "12000.00",
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/landlord/statements{query}&property_id={other_property.pk}"
            ).status_code,
            404,
        )
        annual_mri = self.client.get(
            f"/api/v1/landlord/statements?report=annual_mri&year={today.year}&format=csv&property_id={property_obj.pk}"
        )
        self.assertEqual(annual_mri.status_code, 200)
        self.assertIn(f"landlord-mri-summary-{today.year}.csv", annual_mri["Content-Disposition"])
        self.assertIn("annual_mri", annual_mri.content.decode())
        self.assertIn("tax", annual_mri.content.decode())

        exported = self.client.get(f"/api/v1/landlord/statements{query}&format=csv")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=", exported["Content-Disposition"])
        self.assertIn("collected_invoice_allocations", exported.content.decode())
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="landlord_statement.viewed",
                entity="LandlordStatement",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user=landlord_user,
                action="landlord_statement.exported",
                entity="LandlordStatement",
                new_values__landlord_id=property_obj.landlord_id,
            ).exists()
        )

    def test_property_statement_does_not_allocate_fixed_landlord_fee(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="fixed-fee-statement-landlord@example.com"
        )
        self.client.force_login(landlord_user)
        today = timezone.localdate()
        period_start = today.replace(day=1)
        ManagementAgreement.objects.create(
            landlord=property_obj.landlord,
            effective_from=period_start,
            commission_model="fixed",
            fixed_fee=Decimal("2500.00"),
        )

        response = self.client.get(
            "/api/v1/landlord/statements"
            f"?from={period_start.isoformat()}&to={today.isoformat()}"
            f"&property_id={property_obj.pk}"
        )

        self.assertEqual(response.status_code, 200)
        statement = response.json()
        self.assertEqual(statement["commission_status"], "not_allocated")
        self.assertIsNone(statement["summary"]["commission"])
        self.assertIsNone(statement["summary"]["net_payout"])

    def test_landlord_statement_withholds_net_until_tax_is_configured(self):
        landlord_user, property_obj = self.create_portal_landlord(
            email="statement-tax-pending@example.com"
        )
        ManagementAgreement.objects.create(
            landlord=property_obj.landlord,
            effective_from=timezone.localdate().replace(day=1),
            commission_model="fixed",
            fixed_fee=Decimal("300.00"),
            mri_enabled=True,
        )
        self.client.force_login(landlord_user)
        today = timezone.localdate()
        start = today.replace(day=1)

        response = self.client.get(
            f"/api/v1/landlord/statements?from={start.isoformat()}&to={today.isoformat()}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["commission"], "300.00")
        self.assertIsNone(response.json()["summary"]["tax"])
        self.assertIsNone(response.json()["summary"]["net_payout"])
        self.assertEqual(response.json()["tax_status"], "not_configured")

    def test_landlord_remittance_is_scoped_approved_and_paid_manually(self):
        preparer = self.create_user(
            email="remittance-preparer@example.com",
            phone="254712345620",
        )
        preparer.groups.add(Group.objects.get(name="Property Officer / Agent"))
        preparer.profile.branches.add(self.nairobi)
        approver = self.create_user(
            email="remittance-approver@example.com",
            phone="254712345621",
        )
        approver.groups.add(Group.objects.get(name="Branch Manager"))
        approver.profile.branches.add(self.nairobi)
        finance = self.create_user(
            email="remittance-finance@example.com",
            phone="254712345622",
        )
        finance.groups.add(Group.objects.get(name="Accountant"))
        finance.profile.branches.add(self.nairobi)
        landlord_user, property_obj = self.create_portal_landlord(
            email="remittance-landlord@example.com"
        )
        today = timezone.localdate()
        period_start = today.replace(day=1)
        ManagementAgreement.objects.create(
            landlord=property_obj.landlord,
            effective_from=period_start,
            commission_model="percentage",
            commission_rate=Decimal("10.00"),
            mri_enabled=True,
            terms={
                "landlord_statement_tax": {
                    "name": "MRI",
                    "rate": "5.00",
                    "basis": "collected_invoice_allocations",
                    "approved": True,
                }
            },
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="REMITTANCE-1",
            unit_type="One bedroom",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.nairobi,
            tenant_type=Tenant.INDIVIDUAL,
            name="Remittance Tenant",
            national_id_or_passport="ID-REMITTANCE-1",
            phone="254712345623",
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=period_start,
            due_day=1,
        )
        invoice = Invoice.objects.create(
            branch=self.nairobi,
            tenancy=tenancy,
            number="INV-REMITTANCE-1",
            period_start=period_start,
            period_end=today,
            due_date=today,
            total=Decimal("1000.00"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            charge_type=UnitCharge.CHARGE_RENT,
            description="Monthly rent",
            amount=Decimal("1000.00"),
        )
        payment = Payment.objects.create(
            tenant=tenant,
            amount=Decimal("1000.00"),
            reference="REMITTANCE-COLLECTION-1",
            source=Payment.SOURCE_MPESA,
            received_at=timezone.now(),
            status=Payment.STATUS_ALLOCATED,
            allocated_amount=Decimal("1000.00"),
        )
        PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1000.00"),
        )
        PropertyExpense.objects.create(
            property=property_obj,
            category="Repairs",
            description="Approved repair",
            amount=Decimal("100.00"),
            effective_from=period_start,
            submitted_by=preparer,
            approved_by=approver,
            approved_at=timezone.now(),
            status=PropertyExpense.STATUS_APPROVED,
        )
        maintenance_ticket = MaintenanceTicket.objects.create(
            property=property_obj,
            unit=unit,
            reported_by=preparer,
            title="Repair shared water pump",
            description="Replace the pump seal and test the water system.",
            category=MaintenanceTicket.CATEGORY_PLUMBING,
            status=MaintenanceTicket.STATUS_IN_PROGRESS,
        )
        maintenance_vendor = Vendor.objects.create(
            branch=self.nairobi,
            name="Remittance Water Repairs",
            trade="Plumbing",
            phone="254712345628",
        )
        maintenance_quotation = MaintenanceQuotation.objects.create(
            ticket=maintenance_ticket,
            vendor=maintenance_vendor,
            description="Replace pump seal",
            amount=Decimal("350.00"),
            status=MaintenanceQuotation.STATUS_APPROVED,
            submitted_by=preparer,
            approved_by=approver,
            approved_at=timezone.now(),
        )
        MaintenanceCostAllocation.objects.create(
            quotation=maintenance_quotation,
            target=MaintenanceCostAllocation.TARGET_LANDLORD,
            landlord=property_obj.landlord,
            amount=Decimal("250.00"),
            reason="Owner responsibility",
            allocated_by=preparer,
        )
        MaintenanceCostAllocation.objects.create(
            quotation=maintenance_quotation,
            target=MaintenanceCostAllocation.TARGET_TENANT,
            tenant=tenant,
            amount=Decimal("100.00"),
            reason="Tenant-caused damage",
            allocated_by=preparer,
        )

        self.client.force_login(preparer)
        create_response = self.client.post(
            "/api/v1/landlord/remittances/create",
            data={
                "landlord_id": property_obj.landlord_id,
                "period_start": period_start.isoformat(),
                "period_end": today.isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        remittance_data = create_response.json()["remittance"]
        self.assertEqual(remittance_data["summary"]["collected_invoice_allocations"], "1000.00")
        self.assertEqual(remittance_data["summary"]["commission"], "100.00")
        self.assertEqual(remittance_data["summary"]["expenses"], "100.00")
        self.assertEqual(remittance_data["summary"]["maintenance_costs"], "250.00")
        self.assertEqual(remittance_data["summary"]["tax"], "50.00")
        self.assertEqual(remittance_data["summary"]["net_payout"], "500.00")
        remittance = LandlordRemittance.objects.get(pk=remittance_data["id"])
        self.assertEqual(remittance.status, LandlordRemittance.STATUS_AWAITING_APPROVAL)
        self.assertEqual(remittance.expenses_amount, Decimal("350.00"))
        self.assertEqual(
            len(remittance.statement_snapshot["maintenance_cost_allocations"]),
            1,
        )
        self.assertEqual(
            remittance.statement_snapshot["maintenance_cost_allocations"][0]["amount"],
            "250.00",
        )

        self.assertEqual(
            self.client.post(
                f"/api/v1/landlord/remittances/{remittance.pk}/decision",
                data={"action": "approve"},
                content_type="application/json",
            ).status_code,
            403,
        )
        self.client.force_login(finance)
        self.assertEqual(
            self.client.post(
                f"/api/v1/landlord/remittances/{remittance.pk}/decision",
                data={"action": "approve"},
                content_type="application/json",
            ).status_code,
            403,
        )
        self.client.force_login(approver)
        decision_response = self.client.post(
            f"/api/v1/landlord/remittances/{remittance.pk}/decision",
            data={"action": "approve"},
            content_type="application/json",
        )
        self.assertEqual(decision_response.status_code, 200, decision_response.content)
        self.assertEqual(
            decision_response.json()["remittance"]["status"],
            LandlordRemittance.STATUS_APPROVED,
        )

        self.client.force_login(preparer)
        self.assertEqual(
            self.client.post(
                f"/api/v1/landlord/remittances/{remittance.pk}/payout",
                data={
                    "payout_reference": "MANUAL-REMITTANCE-1",
                    "payout_date": today.isoformat(),
                },
                content_type="application/json",
            ).status_code,
            403,
        )
        self.client.force_login(finance)
        payout_response = self.client.post(
            f"/api/v1/landlord/remittances/{remittance.pk}/payout",
            data={
                "payout_reference": "MANUAL-REMITTANCE-1",
                "payout_date": today.isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(payout_response.status_code, 200, payout_response.content)
        self.assertEqual(payout_response.json()["remittance"]["status"], LandlordRemittance.STATUS_PAID)
        remittance.refresh_from_db()
        self.assertEqual(remittance.payout_recorded_by, finance)
        self.assertTrue(
            AuditLog.objects.filter(
                action="landlord_remittance.prepared",
                entity="LandlordRemittance",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="landlord_remittance.paid",
                entity="LandlordRemittance",
            ).exists()
        )

        self.client.force_login(landlord_user)
        landlord_history = self.client.get("/api/v1/landlord/remittances")
        self.assertEqual(landlord_history.status_code, 200)
        self.assertEqual(len(landlord_history.json()["remittances"]), 1)
        self.assertEqual(
            landlord_history.json()["remittances"][0]["payout_reference"],
            "MANUAL-REMITTANCE-1",
        )
        statement_response = self.client.get(
            f"/api/v1/landlord/statements?from={period_start.isoformat()}&to={today.isoformat()}"
        )
        self.assertEqual(statement_response.status_code, 200)
        statement = statement_response.json()
        self.assertEqual(statement["summary"]["expenses"], "100.00")
        self.assertEqual(statement["summary"]["maintenance_costs"], "250.00")
        self.assertEqual(statement["summary"]["net_payout"], "500.00")
        self.assertEqual(len(statement["maintenance_cost_allocations"]), 1)
        self.assertEqual(
            statement["maintenance_cost_allocations"][0]["ticket_title"],
            "Repair shared water pump",
        )
        statement_csv = self.client.get(
            f"/api/v1/landlord/statements?from={period_start.isoformat()}&to={today.isoformat()}&format=csv"
        )
        self.assertIn("maintenance_costs", statement_csv.content.decode())
        self.assertIn("Repair shared water pump", statement_csv.content.decode())

    def test_landlord_remittance_blocks_unconfigured_tax_and_duplicate_periods(self):
        preparer = self.create_user(
            email="remittance-config-preparer@example.com",
            phone="254712345624",
        )
        preparer.groups.add(Group.objects.get(name="Property Officer / Agent"))
        preparer.profile.branches.add(self.nairobi)
        landlord_user, property_obj = self.create_portal_landlord(
            email="remittance-config-landlord@example.com"
        )
        today = timezone.localdate()
        period_start = today.replace(day=1)
        ManagementAgreement.objects.create(
            landlord=property_obj.landlord,
            effective_from=period_start,
            commission_model="fixed",
            fixed_fee=Decimal("0.00"),
            mri_enabled=True,
        )
        self.client.force_login(preparer)
        request_data = {
            "landlord_id": property_obj.landlord_id,
            "period_start": period_start.isoformat(),
            "period_end": today.isoformat(),
        }
        response = self.client.post(
            "/api/v1/landlord/remittances/create",
            data=request_data,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(LandlordRemittance.objects.exists())

        agreement = ManagementAgreement.objects.get(landlord=property_obj.landlord)
        agreement.mri_enabled = False
        agreement.save(update_fields=["mri_enabled", "updated_at"])
        created = self.client.post(
            "/api/v1/landlord/remittances/create",
            data=request_data,
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        duplicate = self.client.post(
            "/api/v1/landlord/remittances/create",
            data=request_data,
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 409)

        approver = self.create_user(
            email="remittance-zero-approver@example.com",
            phone="254712345626",
        )
        approver.groups.add(Group.objects.get(name="Branch Manager"))
        approver.profile.branches.add(self.nairobi)
        remittance_id = created.json()["remittance"]["id"]
        self.client.force_login(approver)
        rejected = self.client.post(
            f"/api/v1/landlord/remittances/{remittance_id}/decision",
            data={"action": "reject", "review_note": "Please rerun the approval."},
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 200, rejected.content)
        self.client.force_login(preparer)
        revised = self.client.post(
            "/api/v1/landlord/remittances/create",
            data=request_data,
            content_type="application/json",
        )
        self.assertEqual(revised.status_code, 201, revised.content)
        revised_id = revised.json()["remittance"]["id"]
        self.assertEqual(revised.json()["remittance"]["revision"], 2)
        self.client.force_login(approver)
        zero_approval = self.client.post(
            f"/api/v1/landlord/remittances/{revised_id}/decision",
            data={"action": "approve"},
            content_type="application/json",
        )
        self.assertEqual(zero_approval.status_code, 200, zero_approval.content)
        self.assertEqual(
            zero_approval.json()["remittance"]["status"],
            LandlordRemittance.STATUS_NO_PAYOUT,
        )

        unrelated = self.create_user(
            email="remittance-unrelated@example.com",
            phone="254712345625",
        )
        unrelated.groups.add(Group.objects.get(name="Branch Manager"))
        unrelated.profile.branches.add(self.nakuru)
        self.client.force_login(unrelated)
        self.assertEqual(
            self.client.post(
                f"/api/v1/landlord/remittances/{revised_id}/decision",
                data={"action": "approve"},
                content_type="application/json",
            ).status_code,
            404,
        )
        self.client.force_login(landlord_user)
        self.assertEqual(
            self.client.get("/api/v1/landlord/remittances").json()["remittances"][0]["status"],
            LandlordRemittance.STATUS_NO_PAYOUT,
        )

    def test_landlord_statement_requires_portal_access_and_valid_dates(self):
        self.assertEqual(
            self.client.get("/api/v1/landlord/statements?from=2026-01-01&to=2026-01-31").status_code,
            401,
        )
        user = self.create_user(email="statement-non-landlord@example.com")
        user.profile.branches.add(self.nairobi)
        self.client.force_login(user)
        self.assertEqual(
            self.client.get("/api/v1/landlord/statements?from=2026-01-01&to=2026-01-31").status_code,
            403,
        )
        landlord_user, _ = self.create_portal_landlord(email="statement-valid@example.com")
        self.client.force_login(landlord_user)
        self.assertEqual(
            self.client.get("/api/v1/landlord/statements?from=invalid&to=2026-01-31").status_code,
            400,
        )
        self.assertEqual(
            self.client.get("/api/v1/landlord/statements?from=2026-02-01&to=2026-01-31").status_code,
            400,
        )

    def test_public_landlord_registration_is_not_available(self):
        response = self.client.post(
            "/api/v1/auth/register-landlord",
            data={
                "name": "New Landlord",
                "email": "new-landlord@example.com",
                "phone": "254712345679",
                "password": "Long-test-passphrase-29!",
                "property_name": "New Property",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.filter(email="new-landlord@example.com").exists())
        self.assertFalse(Property.objects.filter(name="New Property").exists())

    def test_public_tenant_registration_creates_unlinked_portal_account_and_session(self):
        response = self.client.post(
            "/api/v1/auth/register-tenant",
            data={
                "name": "New Resident",
                "email": "new-resident@example.com",
                "phone": "+254 712 345 681",
                "password": "Long-test-passphrase-29!",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["status"], "awaiting_agency_linkage")
        user = get_user_model().objects.get(email="new-resident@example.com")
        self.assertTrue(user.groups.filter(name="Tenant (portal)").exists())
        self.assertEqual(user.profile.phone, "254712345681")
        self.assertFalse(Tenant.objects.filter(portal_user=user).exists())
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["user"]["id"], user.pk)
        self.assertEqual(self.client.get("/api/v1/tenant/dashboard").status_code, 404)

    def test_public_tenant_registration_rejects_duplicate_contact_and_short_password(self):
        def create(email, phone, password="Long-test-passphrase-29!"):
            return self.client.post(
                "/api/v1/auth/register-tenant",
                data={"name": "Resident", "email": email, "phone": phone, "password": password},
                content_type="application/json",
            )

        self.assertEqual(create("resident-one@example.com", "254712345681").status_code, 201)
        self.client.logout()

        self.assertEqual(create("resident-two@example.com", "254712345681").status_code, 409)
        self.assertEqual(create("resident-one@example.com", "254712345682").status_code, 409)
        self.assertEqual(create("resident-three@example.com", "254712345683", "short").status_code, 400)
        self.assertFalse(get_user_model().objects.filter(email="resident-three@example.com").exists())

    def test_public_vacancies_only_expose_enabled_vacant_units_and_safe_fields(self):
        branch = Branch.objects.get(name="Nairobi")
        property_obj = Property.objects.create(
            branch=branch,
            name="Public Listings Property",
            property_type="residential",
            location="Kahawa",
            security_deposit=Decimal("12000.00"),
            photos=["https://images.example.test/home.jpg", "internal-key"],
        )
        listed_unit = Unit.objects.create(
            property=property_obj,
            code="KHC-B04",
            unit_type="1BR",
            bedrooms=1,
            status=Unit.STATUS_VACANT,
            listing_enabled=True,
        )
        UnitCharge.objects.create(
            unit=listed_unit,
            charge_type=UnitCharge.CHARGE_RENT,
            name="Monthly rent",
            amount=Decimal("18000.00"),
            effective_from=timezone.localdate(),
        )
        Unit.objects.create(
            property=property_obj,
            code="KHC-B05",
            unit_type="1BR",
            status=Unit.STATUS_VACANT,
            listing_enabled=False,
        )
        Unit.objects.create(
            property=property_obj,
            code="KHC-B06",
            unit_type="1BR",
            status=Unit.STATUS_OCCUPIED,
            listing_enabled=True,
        )

        response = self.client.get(
            "/api/v1/public/vacancies?branch=Nairobi&area=Kahawa&type=1BR&rent_max=20000"
        )

        self.assertEqual(response.status_code, 200)
        listings = response.json()["listings"]
        self.assertEqual([item["code"] for item in listings], ["KHC-B04"])
        self.assertEqual(listings[0]["deposit"], "12000.00")
        self.assertEqual(listings[0]["photos"], ["https://images.example.test/home.jpg"])
        self.assertNotIn("id", listings[0])
        self.assertNotIn("branch_id", listings[0])
        self.assertEqual(
            self.client.get("/api/v1/public/vacancies?rent_min=30000&rent_max=10000").status_code,
            400,
        )
        self.assertEqual(
            self.client.get("/api/v1/public/vacancies?rent_min=NaN").status_code,
            400,
        )

    @patch("users.views._send_caretaker_activation_email")
    def test_caretaker_invitation_creates_single_use_account_and_assignment(self, send_email):
        landlord_user, property_obj = self.create_portal_landlord()
        self.client.force_login(landlord_user)

        issued = self.client.post(
            "/api/v1/caretaker-invitations/create",
            data={
                "property_id": property_obj.pk,
                "full_name": "New Caretaker",
                "email": "new-caretaker@example.com",
                "phone": "254712340002",
                "id_or_registration_number": "CARETAKER-ID-002",
                "kra_pin": "A123456789B",
            },
            content_type="application/json",
        )

        self.assertEqual(issued.status_code, 201)
        self.assertNotIn("activation_token", issued.json())
        self.assertEqual(issued.json()["invitation"]["status"], "pending_activation")
        activation_url = send_email.call_args.args[2]
        token = activation_url.split("token=", 1)[1]
        code = send_email.call_args.args[3]
        invitation = CaretakerInvitation.objects.get(email="new-caretaker@example.com")
        self.assertFalse(get_user_model().objects.filter(email="new-caretaker@example.com").exists())
        context = self.client.get(
            f"/api/v1/caretaker-invitations/verify?token={token}"
        )
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.json()["full_name"], "New Caretaker")
        self.assertEqual(context.json()["phone"], "254712340002")
        self.assertNotEqual(invitation.token_hash, token)
        self.assertTrue(invitation.email_sent_at)
        self.assertEqual(
            self.client.get(
                f"/api/v1/caretaker-invitations/verify?token={token}"
            ).status_code,
            200,
        )

        self.client.logout()
        Group.objects.filter(name=CARETAKER_ROLE).delete()
        activated = self.client.post(
            "/api/v1/caretaker-invitations/complete",
            data={
                "token": token,
                "code": code,
                "name": "New Caretaker",
                "phone": "254712340002",
                "password": "Caretaker-secure-Password-92!",
            },
            content_type="application/json",
        )

        self.assertEqual(activated.status_code, 201)
        self.assertTrue(activated.json()["activated"])
        self.assertNotIn("_auth_user_id", self.client.session)
        caretaker_user = get_user_model().objects.get(email="new-caretaker@example.com")
        self.assertTrue(caretaker_user.is_active)
        self.assertTrue(caretaker_user.groups.filter(name=CARETAKER_ROLE).exists())
        caretaker_id = caretaker_user.pk
        self.assertTrue(
            PropertyCaretakerAssignment.objects.filter(
                property=property_obj,
                caretaker_id=caretaker_id,
                ended_at__isnull=True,
            ).exists()
        )
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.used_at)
        self.client.force_login(landlord_user)
        listed = self.client.get("/api/v1/caretaker-invitations")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.json()["invites"][0]["status"], "active")
        self.assertEqual(
            self.client.post(
                "/api/v1/caretaker-invitations/complete",
                data={
                    "token": token,
                    "code": code,
                    "name": "New Caretaker",
                    "phone": "254712340002",
                    "password": "Caretaker-secure-Password-92!",
                },
                content_type="application/json",
            ).status_code,
            400,
        )

    @patch("users.views._send_caretaker_activation_email")
    def test_caretaker_invitation_is_scoped_to_own_property_and_can_be_resent(self, send_email):
        landlord_user, property_obj = self.create_portal_landlord()
        _, other_property = self.create_portal_landlord(
            email="another-landlord@example.com"
        )
        self.client.force_login(landlord_user)

        forbidden = self.client.post(
            "/api/v1/caretaker-invitations/create",
            data={
                "property_id": other_property.pk,
                "full_name": "Unauthorized Caretaker",
                "email": "unauthorized@example.com",
                "phone": "254712340003",
                "id_or_registration_number": "CARETAKER-ID-003",
            },
            content_type="application/json",
        )
        self.assertEqual(forbidden.status_code, 404)

        issued = self.client.post(
            "/api/v1/caretaker-invitations/create",
            data={
                "property_id": property_obj.pk,
                "full_name": "Invited Caretaker",
                "email": "invite@example.com",
                "phone": "254712340004",
                "id_or_registration_number": "CARETAKER-ID-004",
            },
            content_type="application/json",
        )
        invitation_id = issued.json()["invitation"]["id"]
        old_token = send_email.call_args.args[2].split("token=", 1)[1]
        resent = self.client.post(
            f"/api/v1/caretaker-invitations/{invitation_id}/resend",
            content_type="application/json",
        )
        self.assertEqual(resent.status_code, 200)
        new_token = send_email.call_args.args[2].split("token=", 1)[1]
        self.assertNotIn("activation_token", resent.json())
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(
            self.client.get(
                f"/api/v1/caretaker-invitations/verify?token={old_token}"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/caretaker-invitations/verify?token={new_token}"
            ).status_code,
            200,
        )

    @patch("users.views._send_caretaker_activation_email")
    def test_property_officer_can_manage_branch_scoped_caretaker_invitations(self, send_email):
        officer = self.create_user(email="caretaker-onboarding-officer@example.com")
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=officer.profile,
            agency=self.nairobi.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.nairobi)
        property_obj = Property.objects.create(
            branch=self.nairobi,
            name="Officer Managed Property",
            property_type="residential",
            location="Nairobi",
        )
        other_property = Property.objects.create(
            branch=self.nakuru,
            name="Out of Scope Property",
            property_type="residential",
            location="Nakuru",
        )
        self.client.force_login(officer)

        listed = self.client.get("/api/v1/caretaker-invitations")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [item["id"] for item in listed.json()["properties"]],
            [property_obj.pk],
        )

        issued = self.client.post(
            "/api/v1/caretaker-invitations/create",
            data={
                "property_id": property_obj.pk,
                "full_name": "Officer Caretaker",
                "email": "officer-caretaker@example.com",
                "phone": "254712340005",
                "id_or_registration_number": "CARETAKER-ID-005",
            },
            content_type="application/json",
        )
        self.assertEqual(issued.status_code, 201, issued.content)
        invitation_id = issued.json()["invitation"]["id"]
        original_token = send_email.call_args.args[2].split("token=", 1)[1]

        out_of_scope = self.client.post(
            "/api/v1/caretaker-invitations/create",
            data={
                "property_id": other_property.pk,
                "full_name": "Out of Scope Caretaker",
                "email": "out-of-scope@example.com",
                "phone": "254712340006",
                "id_or_registration_number": "CARETAKER-ID-006",
            },
            content_type="application/json",
        )
        self.assertEqual(out_of_scope.status_code, 404)

        resent = self.client.post(
            f"/api/v1/caretaker-invitations/{invitation_id}/resend",
            content_type="application/json",
        )
        self.assertEqual(resent.status_code, 200)
        renewed_token = send_email.call_args.args[2].split("token=", 1)[1]
        self.assertNotIn("activation_token", resent.json())
        self.assertNotEqual(original_token, renewed_token)

        revoked = self.client.post(
            f"/api/v1/caretaker-invitations/{invitation_id}/revoke",
            content_type="application/json",
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["invitation"]["status"], "revoked")
        self.assertEqual(
            self.client.get(
                f"/api/v1/caretaker-invitations/verify?token={renewed_token}"
            ).status_code,
            400,
        )

    def test_phone_only_user_can_sign_in(self):
        user = self.user_model.objects.create_user(
            username="254712345679",
            email="",
            password="Long-test-passphrase-29!",
        )
        UserProfile.objects.create(
            user=user,
            name="Phone Only",
            phone="254712345679",
        )

        response = self.client.post(
            "/api/v1/auth/login",
            data={
                "identifier": "254712345679",
                "password": "Long-test-passphrase-29!",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["user"]["email"])
        self.assertEqual(response.json()["user"]["phone"], "254712345679")

    @override_settings(
        PUBLIC_SITE_URL="https://example.test",
        BREVO_API_KEY="test-api-key",
        BREVO_SENDER_EMAIL="noreply@example.test",
    )
    @patch("users.views._send_password_reset_email")
    def test_password_reset_request_uses_non_enumerating_response(self, send_email):
        user = self.create_user(email="reset@example.com")

        known_response = self.client.post(
            "/api/v1/auth/password-reset",
            data={"email": user.email},
            content_type="application/json",
        )
        unknown_response = self.client.post(
            "/api/v1/auth/password-reset",
            data={"email": "unknown@example.com"},
            content_type="application/json",
        )

        self.assertEqual(known_response.status_code, 200)
        self.assertEqual(unknown_response.status_code, 200)
        self.assertEqual(known_response.json(), unknown_response.json())
        send_email.assert_called_once()
        sent_user, reset_url = send_email.call_args.args
        self.assertEqual(sent_user, user)
        self.assertIn("/reset-password?uid=", reset_url)
        self.assertIn("&token=", reset_url)

    @override_settings(
        PUBLIC_SITE_URL="https://example.test",
        BREVO_API_KEY="test-api-key",
        BREVO_SENDER_EMAIL="noreply@example.test",
    )
    @patch("users.views._send_password_reset_email", side_effect=RuntimeError("provider unavailable"))
    def test_password_reset_email_failure_does_not_disclose_account_existence(self, send_email):
        user = self.create_user(email="reset-failure@example.com")

        known_response = self.client.post(
            "/api/v1/auth/password-reset",
            data={"email": user.email},
            content_type="application/json",
        )
        unknown_response = self.client.post(
            "/api/v1/auth/password-reset",
            data={"email": "unknown-failure@example.com"},
            content_type="application/json",
        )

        self.assertEqual(known_response.status_code, 200)
        self.assertEqual(known_response.json(), unknown_response.json())
        send_email.assert_called_once()

    def test_password_reset_rejects_invalid_link_and_weak_password(self):
        user = self.create_user(email="reset-confirm@example.com")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        weak_password = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            data={"uid": uid, "token": token, "password": "short"},
            content_type="application/json",
        )
        self.assertEqual(weak_password.status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/password-reset/confirm",
                data={"uid": uid, "token": "invalid", "password": "A-strong-password-42!"},
                content_type="application/json",
            ).status_code,
            400,
        )

    def test_password_reset_changes_password_and_invalidates_token(self):
        user = self.create_user(email="reset-complete@example.com")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        response = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            data={"uid": uid, "token": token, "password": "A-new-strong-password-42!"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("A-new-strong-password-42!"))
        self.assertFalse(default_token_generator.check_token(user, token))
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="password_reset.completed",
                entity="User",
            ).exists()
        )

    def test_invalid_password_does_not_create_session(self):
        self.create_user()

        response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": "person@example.com", "password": "wrong"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        event = AuditLog.objects.get(action="login.failed")
        self.assertIsNone(event.user)
        self.assertIsNone(event.old_values)
        self.assertIsNone(event.new_values)

    def test_audit_events_capture_actor_action_entity_time_values_and_ip(self):
        user = self.create_user()
        request = RequestFactory().post("/")
        request.user = user
        request.META["REMOTE_ADDR"] = "192.0.2.10"

        event = record_audit_event(
            request,
            "branch.updated",
            "Branch",
            old_values={"name": "Old"},
            new_values={"name": "New"},
        )

        self.assertEqual(event.user, user)
        self.assertEqual(event.action, "branch.updated")
        self.assertEqual(event.entity, "Branch")
        self.assertIsNotNone(event.timestamp)
        self.assertEqual(event.old_values, {"name": "Old"})
        self.assertEqual(event.new_values, {"name": "New"})
        self.assertEqual(event.ip_address, "192.0.2.10")

    def test_audit_log_is_append_only(self):
        event = AuditLog.objects.create(action="test", entity="Test")

        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=event.pk).update(action="changed")
        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=event.pk).delete()

    def test_audit_log_database_trigger_rejects_direct_edits_and_deletes(self):
        event = AuditLog.objects.create(action="test", entity="Test")

        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE users_auditlog SET action = %s WHERE id = %s",
                    ["changed", event.pk],
                )

        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM users_auditlog WHERE id = %s",
                    [event.pk],
                )

    def test_deleting_audited_user_preserves_event_and_clears_actor(self):
        user = self.create_user()
        user_id = user.pk
        request = RequestFactory().post("/")
        request.user = user
        event = record_audit_event(request, "user.deleted", "User", old_values={"id": user_id})

        user.delete()

        event.refresh_from_db()
        self.assertIsNone(event.user)
        self.assertEqual(event.old_values, {"id": user_id})

    def test_branch_admin_changes_are_audited(self):
        actor = self.create_user()
        request = RequestFactory().post("/")
        request.user = actor
        request.META["REMOTE_ADDR"] = "192.0.2.11"
        branch_admin = BranchAdmin(Branch, admin.site)
        branch = Branch(
            agency=Branch.objects.get(name="Nairobi").agency,
            name="Kisumu",
            town="Kisumu",
        )

        branch_admin.save_model(request, branch, form=None, change=False)
        created = AuditLog.objects.get(action="branch.created", entity="Branch")
        self.assertEqual(created.user, actor)
        self.assertEqual(created.new_values["name"], "Kisumu")
        self.assertEqual(created.ip_address, "192.0.2.11")

        branch.town = "Kisumu Central"
        branch_admin.save_model(request, branch, form=None, change=True)
        updated = AuditLog.objects.get(action="branch.updated", entity="Branch")
        self.assertEqual(updated.old_values["town"], "Kisumu")
        self.assertEqual(updated.new_values["town"], "Kisumu Central")

    def test_role_admin_changes_are_audited(self):
        actor = self.create_user()
        request = RequestFactory().post("/")
        request.user = actor
        role_admin = AuditedGroupAdmin(Group, admin.site)
        role = Group(name="Audit role")
        form = SimpleNamespace(instance=role, save_m2m=lambda: None)

        role_admin.save_model(request, role, form, change=False)
        role_admin.save_related(request, form, [], change=False)
        created = AuditLog.objects.get(action="role.created", entity="Role")
        self.assertEqual(created.user, actor)
        self.assertEqual(created.new_values["name"], "Audit role")

        role.name = "Renamed audit role"
        role_admin.save_model(request, role, form, change=True)
        role_admin.save_related(request, form, [], change=True)
        updated = AuditLog.objects.get(action="role.updated", entity="Role")
        self.assertEqual(updated.old_values["name"], "Audit role")
        self.assertEqual(updated.new_values["name"], "Renamed audit role")

    @override_settings(AUTH_LOGIN_MAX_ATTEMPTS=2, AUTH_LOGIN_LOCKOUT_SECONDS=60)
    def test_repeated_password_failures_lock_the_account(self):
        user = self.create_user()
        data = {"identifier": user.email, "password": "wrong"}

        for _ in range(2):
            response = self.client.post(
                "/api/v1/auth/login",
                data=data,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)

        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.failed_login_attempts, 2)
        self.assertIsNotNone(profile.locked_until)
        locked_response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )
        self.assertEqual(locked_response.status_code, 401)

    def test_finance_roles_require_totp_verification(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["mfa_required"])
        self.assertEqual(response.json()["mfa_method"], "totp")
        self.assertTrue(response.json()["setup_required"])
        self.assertNotIn("code", response.json())
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(TOTPLoginChallenge.objects.count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.totp_challenge_issued",
            ).exists()
        )
        challenge_id = response.json()["challenge_id"]
        challenge = TOTPLoginChallenge.objects.get(pk=challenge_id)
        secret = response.json()["secret"]
        code = _code_for_counter(secret, int(timezone.now().timestamp() // 30))
        invalid_code = next(
            candidate
            for candidate in (f"{value:06d}" for value in range(1_000_000))
            if not verify_code(secret, candidate)
        )
        rejected = self.client.post(
            "/api/v1/auth/mfa/totp/verify",
            data={
                "challenge_id": challenge_id,
                "code": invalid_code,
            },
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(TOTPLoginChallenge.objects.get().attempts, 1)

        verified = self.client.post(
            "/api/v1/auth/mfa/totp/verify",
            data={"challenge_id": challenge_id, "code": code},
            content_type="application/json",
        )

        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["user"]["id"], user.pk)
        self.assertEqual(len(verified.json()["recovery_codes"]), 10)
        self.assertFalse(TOTPLoginChallenge.objects.exists())
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.totp_enabled)
        self.assertNotEqual(bytes(user.profile.totp_secret_encrypted).decode("latin1"), secret)
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.succeeded",
            ).exists()
        )

    def test_enrolled_totp_user_can_sign_in_and_consume_a_recovery_code_once(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        secret = "JBSWY3DPEHPK3PXP"
        user.profile.totp_secret_encrypted = encrypt_secret(secret)
        user.profile.totp_enabled = True
        user.profile.totp_recovery_codes = [make_password("A1B2C3D4")]
        user.profile.save(
            update_fields=["totp_secret_encrypted", "totp_enabled", "totp_recovery_codes"]
        )

        challenged = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )
        self.assertEqual(challenged.status_code, 202)
        self.assertFalse(challenged.json()["setup_required"])
        self.assertNotIn("secret", challenged.json())
        challenge_id = challenged.json()["challenge_id"]
        verified = self.client.post(
            "/api/v1/auth/mfa/totp/verify",
            data={"challenge_id": challenge_id, "code": "A1B2-C3D4"},
            content_type="application/json",
        )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.totp_recovery_codes, [])

        self.client.post("/api/v1/auth/logout", content_type="application/json")
        replay_challenge = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )
        self.assertEqual(replay_challenge.status_code, 202)
        replay = self.client.post(
            "/api/v1/auth/mfa/totp/verify",
            data={"challenge_id": replay_challenge.json()["challenge_id"], "code": "A1B2C3D4"},
            content_type="application/json",
        )
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_totp_code_cannot_be_reused_for_another_login_challenge(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        user.profile.totp_secret_encrypted = encrypt_secret("JBSWY3DPEHPK3PXP")
        user.profile.totp_enabled = True
        user.profile.save(update_fields=["totp_secret_encrypted", "totp_enabled"])

        with patch("users.views.matching_counter", return_value=42):
            first_challenge = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )
            first_verified = self.client.post(
                "/api/v1/auth/mfa/totp/verify",
                data={
                    "challenge_id": first_challenge.json()["challenge_id"],
                    "code": "123456",
                },
                content_type="application/json",
            )
            self.assertEqual(first_verified.status_code, 200)
            self.client.post("/api/v1/auth/logout", content_type="application/json")
            second_challenge = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )
            replay = self.client.post(
                "/api/v1/auth/mfa/totp/verify",
                data={
                    "challenge_id": second_challenge.json()["challenge_id"],
                    "code": "123456",
                },
                content_type="application/json",
            )

        self.assertEqual(replay.status_code, 401)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.totp_last_used_counter, 42)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_fifth_invalid_totp_code_consumes_challenge(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        challenge_response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )
        challenge_id = challenge_response.json()["challenge_id"]
        secret = challenge_response.json()["secret"]
        invalid_code = next(
            candidate
            for candidate in (f"{value:06d}" for value in range(1_000_000))
            if not verify_code(secret, candidate)
        )

        for _ in range(5):
            rejected = self.client.post(
                "/api/v1/auth/mfa/totp/verify",
                data={"challenge_id": challenge_id, "code": invalid_code},
                content_type="application/json",
            )

        self.assertEqual(rejected.status_code, 401)
        self.assertFalse(TOTPLoginChallenge.objects.filter(pk=challenge_id).exists())
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_login_requires_csrf_token(self):
        user = self.create_user()
        client = Client(enforce_csrf_checks=True)
        login_data = {
            "identifier": user.email,
            "password": "Long-test-passphrase-29!",
        }

        rejected = client.post(
            "/api/v1/auth/login",
            data=login_data,
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 403)

        csrf_response = client.get("/api/v1/auth/csrf")
        accepted = client.post(
            "/api/v1/auth/login",
            data=login_data,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrfToken"],
        )
        self.assertEqual(accepted.status_code, 200)

    def test_console_sms_backend_fails_closed_outside_debug(self):
        from users.sms import ConsoleSMSBackend

        with override_settings(DEBUG=False):
            with self.assertRaises(ImproperlyConfigured):
                ConsoleSMSBackend().send("254712345678", "test")

    def test_sms_templates_queue_encrypted_delivery_and_log_provider_result(self):
        user = self.create_user(
            email="sms-outbox@example.com",
            phone="254712345630",
        )
        code = "928314"
        delivery = queue_template_sms(
            user.profile.phone,
            "login_verification_code",
            {"code": code},
            actor=user,
        )

        self.assertEqual(delivery.status, SMSDelivery.STATUS_QUEUED)
        self.assertNotIn(code.encode(), bytes(delivery.message_encrypted))
        self.assertNotIn(user.profile.phone.encode(), bytes(delivery.recipient_encrypted))
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="sms.queued",
                entity="SMSDelivery",
            ).exists()
        )
        with patch(
            "users.sms.send_sms",
            return_value={"message_id": "provider-message-001"},
        ) as send:
            dispatched = dispatch_next_sms_delivery()

        self.assertEqual(dispatched.pk, delivery.pk)
        self.assertEqual(dispatched.status, SMSDelivery.STATUS_SENT)
        self.assertEqual(dispatched.provider_message_id, "provider-message-001")
        self.assertEqual(dispatched.recipient_encrypted, b"")
        self.assertEqual(dispatched.message_encrypted, b"")
        self.assertEqual(send.call_args.args[0], user.profile.phone)
        self.assertIn(code, send.call_args.args[1])
        self.assertEqual(
            send.call_args.kwargs["idempotency_key"],
            f"sms-delivery-{delivery.pk}",
        )
        self.assertTrue(
            AuditLog.objects.filter(
                user__isnull=True,
                action="sms.sent",
                entity="SMSDelivery",
                new_values__created_by_id=user.pk,
            ).exists()
        )

    @override_settings(
        AUTH_SMS_BACKEND="users.sms.SoftlinkSMSBackend",
        SOFTLINK_SMS_API_KEY="test-softlink-key",
        SOFTLINK_SMS_API_URL="https://api.softlinksms.co.ke/api/v1/sms",
        SOFTLINK_SMS_TRAFFIC_CLASS="promotional",
    )
    def test_softlink_sms_backend_sends_secure_idempotent_request(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"message_id":"sms-123"}'
        with patch("users.sms.urlopen", return_value=response) as urlopen:
            result = send_sms(
                "254716060198",
                "482913 is your Legacy Real Estate sign-in code.",
                idempotency_key="sms-delivery-42",
            )

        self.assertEqual(result, "sms-123")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.softlinksms.co.ke/api/v1/sms")
        self.assertEqual(request.get_header("X-api-key"), "test-softlink-key")
        self.assertEqual(request.get_header("Idempotency-key"), "sms-delivery-42")
        self.assertEqual(
            json.loads(request.data),
            {
                "recipients": ["+254716060198"],
                "body": "482913 is your Legacy Real Estate sign-in code.",
                "traffic_class": "promotional",
            },
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)

    @override_settings(
        AUTH_SMS_BACKEND="users.sms.SoftlinkSMSBackend",
        SOFTLINK_SMS_API_KEY="",
    )
    def test_softlink_sms_backend_requires_secret_without_making_request(self):
        with patch("users.sms.urlopen") as urlopen:
            with self.assertRaises(ImproperlyConfigured):
                send_sms(
                    "254716060198",
                    "Verification code",
                    idempotency_key="sms-delivery-43",
                )
        urlopen.assert_not_called()

    @override_settings(
        AUTH_SMS_BACKEND="users.sms.SoftlinkSMSBackend",
        SOFTLINK_SMS_API_KEY="",
    )
    def test_finance_login_does_not_depend_on_sms_provider(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))

        response = self.client.post(
            "/api/v1/auth/login",
            data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["mfa_method"], "totp")
        self.assertFalse(SMSLoginChallenge.objects.exists())
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEBUG=True,
        BREVO_API_KEY="",
    )
    def test_local_password_recovery_email_uses_configured_email_backend(self):
        from django.core import mail

        user = self.create_user(email="mailpit-test@example.com")
        with patch("users.views.settings.PUBLIC_SITE_URL", "http://localhost:3000"):
            from users.views import _send_password_reset_email

            _send_password_reset_email(user, "http://localhost:3000/reset-password?token=test")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn("reset-password", mail.outbox[0].body)

    def test_sms_delivery_failures_retry_and_expired_messages_are_discarded(self):
        user = self.create_user(
            email="sms-retry@example.com",
            phone="254712345631",
        )
        queue_template_sms(
            user.profile.phone,
            "payment_receipt",
            {
                "reference": "RECEIPT-1",
                "amount": "500.00",
                "agency_name": "Legacy Real Estate",
            },
            actor=user,
        )
        with patch("users.sms.send_sms", side_effect=RuntimeError("private provider response")):
            failed_attempt = dispatch_next_sms_delivery()
        self.assertEqual(failed_attempt.status, SMSDelivery.STATUS_QUEUED)
        self.assertEqual(failed_attempt.attempts, 1)
        self.assertEqual(failed_attempt.error_code, "RuntimeError")
        self.assertNotIn("private provider response", failed_attempt.error_code)

        with patch("users.sms.send_sms", return_value=None):
            successful_attempt = dispatch_next_sms_delivery(
                now=failed_attempt.available_at + timedelta(seconds=1)
            )
        self.assertEqual(successful_attempt.status, SMSDelivery.STATUS_SENT)
        self.assertEqual(successful_attempt.attempts, 2)

        expired = queue_template_sms(
            user.profile.phone,
            "login_verification_code",
            {"code": "123456"},
            actor=user,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with patch("users.sms.send_sms") as send:
            expired_attempt = dispatch_next_sms_delivery()
        self.assertEqual(expired_attempt.pk, expired.pk)
        self.assertEqual(expired_attempt.status, SMSDelivery.STATUS_EXPIRED)
        self.assertEqual(expired_attempt.message_encrypted, b"")
        send.assert_not_called()

        terminal = queue_template_sms(
            user.profile.phone,
            "payment_receipt",
            {
                "reference": "RECEIPT-2",
                "amount": "250.00",
                "agency_name": "Legacy Real Estate",
            },
            actor=user,
        )
        with patch("users.sms.send_sms", side_effect=RuntimeError("provider unavailable")):
            terminal_failure = dispatch_next_sms_delivery(max_attempts=1)
        self.assertEqual(terminal_failure.pk, terminal.pk)
        self.assertEqual(terminal_failure.status, SMSDelivery.STATUS_FAILED)
        self.assertEqual(terminal_failure.recipient_encrypted, b"")
        self.assertEqual(terminal_failure.message_encrypted, b"")

    def test_sms_template_rejects_unknown_names_and_missing_placeholders(self):
        with self.assertRaises(ValueError):
            render_sms_template("unknown_template", {})
        with self.assertRaises(ValueError):
            render_sms_template("login_verification_code", {})

    def test_sms_authentication_fails_closed_without_a_production_provider(self):
        with override_settings(DEBUG=False, AUTH_SMS_BACKEND=""):
            with self.assertRaises(ImproperlyConfigured):
                send_sms("254712345678", "test")
            with self.assertRaises(ImproperlyConfigured):
                queue_template_sms(
                    "254712345678",
                    "login_verification_code",
                    {"code": "123456"},
                )

    def test_landlord_profile_and_onboarding_are_django_backed_and_role_scoped(self):
        user, property_obj = self.create_portal_landlord()
        self.client.force_login(user)

        profile_response = self.client.get("/api/v1/landlord/profile")
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(
            profile_response.json()["profile"],
            {
                "full_name": "Example Person",
                "email": user.email,
                "phone": user.profile.phone,
            },
        )

        onboarding_response = self.client.get("/api/v1/landlord/onboarding")
        self.assertEqual(onboarding_response.status_code, 200)
        self.assertEqual(onboarding_response.json()["propertyName"], property_obj.name)
        self.assertFalse(onboarding_response.json()["onboardingCompleted"])

        updated = self.client.patch(
            "/api/v1/landlord/profile",
            data={"fullName": "Updated Landlord", "phone": "0712345678"},
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.name, "Updated Landlord")
        self.assertEqual(user.profile.phone, "254712345678")

        completed = self.client.post(
            "/api/v1/landlord/onboarding",
            data={"tutorialCompleted": False},
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.onboarding_completed)
        self.assertFalse(user.profile.tutorial_completed)

        other_user = self.create_user(
            email="not-a-landlord@example.com",
            phone="254712399999",
        )
        self.client.force_login(other_user)
        self.assertEqual(self.client.get("/api/v1/landlord/profile").status_code, 403)

    def test_caretaker_profile_and_onboarding_are_django_backed(self):
        user = self.create_user(email="caretaker-profile@example.com")
        user.groups.add(Group.objects.get(name="Caretaker"))
        self.client.force_login(user)

        profile_response = self.client.get("/api/v1/caretaker/profile")
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(profile_response.json()["profile"]["role"], "caretaker")

        updated = self.client.patch(
            "/api/v1/caretaker/profile",
            data={
                "fullName": "Updated Caretaker",
                "email": "updated-caretaker@example.com",
                "phone": "+254 712 345 678",
            },
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200)
        user.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertEqual(user.email, "updated-caretaker@example.com")
        self.assertEqual(user.username, "updated-caretaker@example.com")
        self.assertEqual(user.profile.name, "Updated Caretaker")
        self.assertEqual(user.profile.phone, "254712345678")

        completed = self.client.post(
            "/api/v1/caretaker/onboarding",
            data={},
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.onboarding_completed)
        self.assertTrue(user.profile.tutorial_completed)


class PropertyOfficerTenantOnboardingTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.get(name="Nairobi")
        self.officer = get_user_model().objects.create_user(
            username="move-in-officer@example.com",
            email="move-in-officer@example.com",
            password="secure-password",
        )
        self.profile = UserProfile.objects.create(
            user=self.officer,
            name="Move-in Officer",
            phone="254712340001",
        )
        membership, _ = AgencyMembership.objects.get_or_create(
            profile=self.profile,
            agency=self.branch.agency,
        )
        membership.roles.add(Group.objects.get(name="Property Officer / Agent"))
        membership.branches.add(self.branch)
        self.property = Property.objects.create(
            branch=self.branch,
            name="Onboarding Quote Property",
            property_type="residential",
            location="Nairobi",
            default_rent=Decimal("10000.00"),
            security_deposit=Decimal("9000.00"),
            water_deposit=Decimal("0.00"),
        )
        self.unit_type = PropertyUnitType.objects.create(
            property=self.property,
            name="Bedsitter",
            monthly_rent=Decimal("12000.00"),
            security_deposit=Decimal("5000.00"),
        )
        PropertyUnitTypeUtility.objects.create(
            unit_type=self.unit_type,
            utility_type=PropertyUnitTypeUtility.UTILITY_WATER,
            name="Water",
            billing_method=PropertyUnitTypeUtility.METHOD_FIXED,
            fixed_amount=Decimal("300.00"),
            deposit_amount=Decimal("1000.00"),
        )
        PropertyUnitTypeUtility.objects.create(
            unit_type=self.unit_type,
            utility_type=PropertyUnitTypeUtility.UTILITY_ELECTRICITY,
            name="Electricity",
            billing_method=PropertyUnitTypeUtility.METHOD_METERED,
            deposit_amount=Decimal("2000.00"),
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_type_definition=self.unit_type,
            code="ONB-01",
            unit_type="Bedsitter",
            status=Unit.STATUS_VACANT,
        )
        self.client.force_login(self.officer)

    def _onboarding_payload(self, quote, **overrides):
        payload = {
            "unit_id": self.unit.pk,
            "start_date": timezone.localdate().isoformat(),
            "end_date": None,
            "tenancy_type": Tenancy.TYPE_MONTH_TO_MONTH,
            "due_day": 10,
            "tenant_type": Tenant.INDIVIDUAL,
            "name": "Onboarded Tenant",
            "email": "onboarded-tenant@example.com",
            "phone": "0712345678",
            "national_id_or_passport": "ID-ONBOARD-1",
            "company_registration_number": "",
            "contact_person": "",
            "kra_pin": "",
            "confirm_paid": True,
            "quote_total": quote["total"],
            "quote_fingerprint": quote["fingerprint"],
        }
        payload.update(overrides)
        return payload

    @patch("users.views.queue_template_sms")
    @patch("users.views._send_tenant_activation_email")
    def test_onboarding_quotes_all_deposits_and_requires_audited_payment_confirmation(
        self,
        send_activation_email,
        queue_sms,
    ):
        quoted = self.client.get(
            "/api/v1/property-officer/move-in-quote",
            {"unit_id": self.unit.pk, "start_date": timezone.localdate().isoformat()},
        )
        self.assertEqual(quoted.status_code, 200, quoted.content)
        quote = quoted.json()["quote"]
        self.assertEqual(quote["total"], "20000.00")
        self.assertEqual(
            {item["name"]: item["amount"] for item in quote["items"]},
            {
                "Monthly rent": "12000.00",
                "Security deposit": "5000.00",
                "Water deposit": "1000.00",
                "Electricity deposit": "2000.00",
            },
        )

        payload = self._onboarding_payload(quote, confirm_paid=False)
        unconfirmed = self.client.post(
            "/api/v1/property-officer/tenant-onboarding",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.content)
        self.assertFalse(Tenant.objects.exists())
        self.assertFalse(MoveInSettlement.objects.exists())

        stale_quote = self.client.post(
            "/api/v1/property-officer/tenant-onboarding",
            data=self._onboarding_payload(quote, quote_total="1.00"),
            content_type="application/json",
        )
        self.assertEqual(stale_quote.status_code, 409, stale_quote.content)
        self.assertFalse(Tenant.objects.exists())
        self.assertFalse(MoveInSettlement.objects.exists())

        onboarded = self.client.post(
            "/api/v1/property-officer/tenant-onboarding",
            data=self._onboarding_payload(quote),
            content_type="application/json",
        )
        self.assertEqual(onboarded.status_code, 201, onboarded.content)
        self.assertTrue(onboarded.json()["activation_email_sent"])
        self.assertTrue(onboarded.json()["activation_sms_queued"])
        self.assertNotIn("token", onboarded.json())
        self.assertNotIn("code", onboarded.json())

        tenant = Tenant.objects.get(email="onboarded-tenant@example.com")
        tenancy = Tenancy.objects.get(tenant=tenant, unit=self.unit)
        settlement = MoveInSettlement.objects.get(unit=self.unit)
        payment = MoveInPayment.objects.get(settlement=settlement)
        self.assertTrue(tenant.is_provisional)
        self.assertEqual(tenancy.status, Tenancy.STATUS_ACTIVE)
        self.assertEqual(settlement.status, MoveInSettlement.STATUS_PAID)
        self.assertEqual(payment.amount, Decimal("20000.00"))
        self.assertEqual(payment.method, MoveInPayment.METHOD_OFFICER_CONFIRMATION)
        self.assertEqual(payment.recorded_by, self.officer)
        self.assertEqual(settlement.deposit_total, Decimal("8000.00"))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, Unit.STATUS_OCCUPIED)
        self.assertTrue(
            AuditLog.objects.filter(
                action="move_in.payment_confirmed",
                new_values__move_in_payment_id=payment.pk,
            ).exists()
        )
        send_activation_email.assert_called_once()
        queue_sms.assert_called_once()
        self.assertEqual(queue_sms.call_args.args[1], "tenant_activation")
        self.assertEqual(queue_sms.call_args.args[2]["code"], send_activation_email.call_args.args[5])

    def test_move_in_quote_and_onboarding_are_limited_to_officer_branches(self):
        other_branch = Branch.objects.get(name="Nakuru")
        other_property = Property.objects.create(
            branch=other_branch,
            name="Out of scope property",
            property_type="residential",
            location="Nakuru",
        )
        other_unit = Unit.objects.create(
            property=other_property,
            code="OUT-01",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
        )
        quote = self.client.get(
            "/api/v1/property-officer/move-in-quote",
            {"unit_id": other_unit.pk, "start_date": timezone.localdate().isoformat()},
        )
        self.assertEqual(quote.status_code, 404)

    @patch("users.views.queue_template_sms")
    @patch(
        "users.views._send_tenant_activation_email",
        side_effect=[RuntimeError("mail service unavailable"), None],
    )
    def test_activation_message_retry_reuses_confirmed_payment_after_quote_changes(
        self,
        send_activation_email,
        queue_sms,
    ):
        quote_response = self.client.get(
            "/api/v1/property-officer/move-in-quote",
            {"unit_id": self.unit.pk, "start_date": timezone.localdate().isoformat()},
        )
        quote = quote_response.json()["quote"]
        payload = self._onboarding_payload(quote)
        first_attempt = self.client.post(
            "/api/v1/property-officer/tenant-onboarding",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(first_attempt.status_code, 201, first_attempt.content)
        self.assertFalse(first_attempt.json()["activation_email_sent"])
        self.assertTrue(first_attempt.json()["activation_sms_queued"])

        self.unit_type.monthly_rent = Decimal("13000.00")
        self.unit_type.save(update_fields=["monthly_rent"])
        retried = self.client.post(
            "/api/v1/property-officer/tenant-onboarding",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(retried.status_code, 201, retried.content)
        self.assertTrue(retried.json()["activation_email_sent"])
        self.assertTrue(retried.json()["activation_sms_queued"])
        self.assertEqual(MoveInPayment.objects.count(), 1)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Tenancy.objects.count(), 1)
        self.assertEqual(send_activation_email.call_count, 2)
        self.assertEqual(queue_sms.call_count, 2)


class MoveInSettlementTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.get(name="Nairobi")
        user_model = get_user_model()
        self.landlord_user = user_model.objects.create_user(
            username="move-in-landlord@example.com",
            email="move-in-landlord@example.com",
            password="secure-password",
        )
        self.landlord_user.groups.add(Group.objects.get(name="Landlord (portal)"))
        self.profile = UserProfile.objects.create(
            user=self.landlord_user,
            name="Move-in Landlord",
            phone="254712340001",
        )
        landlord = Landlord.objects.create(
            branch=self.branch,
            portal_user=self.landlord_user,
            name="Move-in Landlord",
            id_or_registration_number="MOVEIN-LANDLORD-1",
            phone="254712340001",
            email="move-in-landlord@example.com",
        )
        self.property = Property.objects.create(
            branch=self.branch,
            landlord=landlord,
            name="Move-in Property",
            property_type="residential",
            location="Nairobi",
        )
        self.profile.active_property = self.property
        self.profile.save(update_fields=["active_property"])
        self.unit = Unit.objects.create(
            property=self.property,
            code="MI-101",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
        )
        self.client.force_login(self.landlord_user)

    def _create_reservation(self, **overrides):
        payload = {
            "unitId": self.unit.pk,
            "tenantName": "Move-in Tenant",
            "tenantEmail": "move-in-tenant@example.com",
            "tenantPhone": "0712345678",
            "expectedMoveInDate": timezone.localdate().isoformat(),
            "depositAmount": "2000",
            "rentAmount": "10000",
            "garbageAmount": "300",
            "waterDepositAmount": "500",
        }
        payload.update(overrides)
        return self.client.post(
            "/api/v1/landlord/move-in-settlements",
            data=payload,
            content_type="application/json",
        )

    def test_move_in_reservation_uses_postgres_recurring_charges_and_scopes_unit(self):
        PropertyRecurringCharge.objects.create(
            property=self.property,
            name="Service charge",
            amount=Decimal("150.00"),
        )
        created = self._create_reservation()
        self.assertEqual(created.status_code, 201, created.content)
        settlement = MoveInSettlement.objects.get()
        self.assertEqual(settlement.tenant_phone, "254712345678")
        self.assertEqual(settlement.total_required, Decimal("12950.00"))
        self.assertEqual(settlement.outstanding_balance, Decimal("12950.00"))
        self.assertEqual(settlement.utility_charges, [{"name": "Service charge", "amount": "150.00"}])
        self.assertEqual(created.json()["settlement"]["units"]["unit_code"], self.unit.code)
        self.assertEqual(self.unit.status, Unit.STATUS_VACANT)
        self.assertEqual(self._create_reservation().status_code, 409)
        self.assertTrue(
            AuditLog.objects.filter(
                action="move_in_settlement.created",
                entity="MoveInSettlement",
                user=self.landlord_user,
            ).exists()
        )

    @patch("users.views._send_tenant_activation_email")
    def test_manual_payments_are_atomic_book_and_require_full_balance_for_activation(self, send_email):
        created = self._create_reservation()
        settlement_id = created.json()["settlement"]["id"]
        deposit_payment = self.client.patch(
            "/api/v1/landlord/move-in-settlements",
            data={
                "settlementId": settlement_id,
                "amount": "2500",
                "mpesaReference": "  dep-101 ",
            },
            content_type="application/json",
        )
        self.assertEqual(deposit_payment.status_code, 200, deposit_payment.content)
        settlement = MoveInSettlement.objects.get(pk=settlement_id)
        self.unit.refresh_from_db()
        self.assertEqual(settlement.status, MoveInSettlement.STATUS_BOOKED)
        self.assertEqual(settlement.deposit_paid_amount, Decimal("2500.00"))
        self.assertEqual(self.unit.status, Unit.STATUS_BOOKED)
        self.assertTrue(deposit_payment.json()["bookingConfirmed"])
        self.assertFalse(deposit_payment.json()["activationReady"])
        too_much = self.client.patch(
            "/api/v1/landlord/move-in-settlements",
            data={"settlementId": settlement_id, "amount": "11000"},
            content_type="application/json",
        )
        self.assertEqual(too_much.status_code, 400)
        self.assertEqual(MoveInPayment.objects.filter(settlement=settlement).count(), 1)

        balance_payment = self.client.patch(
            "/api/v1/landlord/move-in-settlements",
            data={"settlementId": settlement_id, "amount": "10300"},
            content_type="application/json",
        )
        self.assertEqual(balance_payment.status_code, 200, balance_payment.content)
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, MoveInSettlement.STATUS_PAID)
        self.assertEqual(settlement.outstanding_balance, Decimal("0.00"))
        self.assertTrue(balance_payment.json()["activationReady"])
        send_email.assert_called_once()
        self.assertEqual(MoveInPayment.objects.filter(settlement=settlement).count(), 2)

    @patch("users.views._send_tenant_activation_email")
    def test_activation_requires_identity_and_creates_tenancy_deposit_and_portal(self, send_email):
        created = self._create_reservation()
        settlement_id = created.json()["settlement"]["id"]
        self.client.patch(
            "/api/v1/landlord/move-in-settlements",
            data={"settlementId": settlement_id, "amount": "12800"},
            content_type="application/json",
        )
        activation = self.client.patch(
            "/api/v1/landlord/move-in-settlements",
            data={"settlementId": settlement_id, "sendActivationEmail": True},
            content_type="application/json",
        )
        self.assertEqual(activation.status_code, 200, activation.content)
        send_email.assert_called_once()
        settlement = MoveInSettlement.objects.get(pk=settlement_id)
        tenant = settlement.tenant
        self.assertTrue(tenant.is_provisional)
        self.assertEqual(tenant.national_id_or_passport, "")
        tenancy = Tenancy.objects.get(unit=self.unit, tenant=tenant)
        deposit = DepositAccount.objects.get(tenancy=tenancy)
        self.assertEqual(deposit.target_amount, Decimal("2500.00"))
        self.assertEqual(deposit.balance, Decimal("2500.00"))
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, Unit.STATUS_OCCUPIED)

        activation_url = send_email.call_args.args[-2]
        activation_code = send_email.call_args.args[-1]
        token = activation_url.split("token=", 1)[1]
        missing_identity = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={"token": token, "code": activation_code, "password": "Tenant-secure-password-91!"},
            content_type="application/json",
        )
        self.assertEqual(missing_identity.status_code, 400)
        self.assertEqual(missing_identity.json()["title"], "Identity required")
        self.client.logout()
        completed = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={
                "token": token,
                "code": activation_code,
                "password": "Tenant-secure-password-91!",
                "national_id_or_passport": "ID-MOVE-IN-101",
            },
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        tenant.refresh_from_db()
        settlement.refresh_from_db()
        self.assertFalse(tenant.is_provisional)
        self.assertEqual(tenant.national_id_or_passport, "ID-MOVE-IN-101")
        self.assertEqual(settlement.status, MoveInSettlement.STATUS_ACTIVE)
        self.assertNotIn("_auth_user_id", self.client.session)
        login = self.client.post(
            "/api/v1/auth/login",
            data={
                "identifier": tenant.email,
                "password": "Tenant-secure-password-91!",
            },
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200, login.content)
        self.assertIn(TENANT_PORTAL_ROLE, login.json()["user"]["roles"])
        profile_response = self.client.get("/api/v1/tenant/profile")
        self.assertEqual(
            profile_response.status_code,
            200,
            f"login={login.content!r}; profile={profile_response.content!r}",
        )
        self.assertEqual(profile_response.json()["profile"]["unitCode"], "MI-101")
        self.assertEqual(self.client.get("/api/v1/tenant/unit-status").json()["status"], "occupied")

    @patch("users.views._send_tenant_activation_email")
    @override_settings(PUBLIC_SITE_URL="https://legacy.example/")
    def test_activation_materializes_settlement_charges_for_future_invoices(self, send_email):
        from users.billing import generate_monthly_invoice

        PropertyRecurringCharge.objects.create(
            property=self.property,
            name="Service charge",
            amount=Decimal("150.00"),
        )
        today = timezone.localdate()
        move_in_date = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        created = self._create_reservation(expectedMoveInDate=move_in_date.isoformat())
        settlement_id = created.json()["settlement"]["id"]
        paid = self.client.patch(
            "/api/v1/landlord/move-in-settlements",
            data={"settlementId": settlement_id, "amount": "12950"},
            content_type="application/json",
        )
        self.assertEqual(paid.status_code, 200, paid.content)
        activation = self.client.patch(
            "/api/v1/landlord/move-in-settlements",
            data={"settlementId": settlement_id, "sendActivationEmail": True},
            content_type="application/json",
        )
        self.assertEqual(activation.status_code, 200, activation.content)
        self.assertTrue(
            send_email.call_args.args[-2].startswith(
                "https://legacy.example/activate?token="
            )
        )

        tenancy = Tenancy.objects.get(unit=self.unit)
        effective_charges = list(
            UnitCharge.objects.filter(
                unit=self.unit,
                effective_from=move_in_date,
            ).order_by("charge_type", "name")
        )
        self.assertEqual(
            [(charge.charge_type, charge.name, charge.amount) for charge in effective_charges],
            [
                (UnitCharge.CHARGE_RECURRING, "Garbage", Decimal("300.00")),
                (UnitCharge.CHARGE_RECURRING, "Service charge", Decimal("150.00")),
                (UnitCharge.CHARGE_RENT, "Rent", Decimal("10000.00")),
            ],
        )

        invoice, was_created = generate_monthly_invoice(tenancy, move_in_date)
        self.assertTrue(was_created)
        self.assertEqual(invoice.total, Decimal("10450.00"))
        self.assertEqual(
            set(invoice.lines.values_list("description", flat=True)),
            {"Rent", "Garbage", "Service charge"},
        )

    def test_landlord_unit_list_and_occupancy_manage_use_django_units(self):
        units = self.client.get("/api/v1/landlord/move-in-units")
        self.assertEqual(units.status_code, 200, units.content)
        self.assertEqual(units.json()["units"][0]["unitCode"], "MI-101")
        self.assertEqual(units.json()["units"][0]["status"], "vacant")

        changed = self.client.post(
            "/api/v1/units/occupancy",
            data={"unitId": self.unit.pk, "status": "maintenance"},
            content_type="application/json",
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, Unit.STATUS_MAINTENANCE)

        move_out = self.client.post(
            "/api/v1/units/manage",
            data={"unitId": self.unit.pk, "action": "offboard_tenant"},
            content_type="application/json",
        )
        self.assertEqual(move_out.status_code, 410)

    def test_move_in_stk_callback_is_idempotent_and_rejects_amount_mismatch(self):
        created = self._create_reservation()
        settlement_id = created.json()["settlement"]["id"]
        attempt = self.client.post(
            "/api/v1/move-in-settlements/stk-payment",
            data={"action": "create", "settlementId": settlement_id, "amount": "2500"},
            content_type="application/json",
        )
        self.assertEqual(attempt.status_code, 201, attempt.content)
        payment_id = attempt.json()["paymentId"]
        attached = self.client.post(
            "/api/v1/move-in-settlements/stk-payment",
            data={
                "action": "attach",
                "paymentId": payment_id,
                "checkoutRequestId": "ws_CO_MOVEIN_1",
            },
            content_type="application/json",
        )
        self.assertEqual(attached.status_code, 200, attached.content)
        callback_payload = {
            "checkout_request_id": "ws_CO_MOVEIN_1",
            "result_code": 0,
            "amount": 2500,
            "reference": "QWE123456",
        }
        applied = self.client.post(
            "/api/v1/payments/mpesa/move-in-callback",
            data=callback_payload,
            content_type="application/json",
        )
        self.assertEqual(applied.status_code, 200, applied.content)
        self.assertTrue(applied.json()["applied"])
        duplicate = self.client.post(
            "/api/v1/payments/mpesa/move-in-callback",
            data=callback_payload,
            content_type="application/json",
        )
        self.assertTrue(duplicate.json()["duplicate"])
        settlement = MoveInSettlement.objects.get(pk=settlement_id)
        self.assertEqual(settlement.total_paid, Decimal("2500.00"))
        self.assertEqual(MoveInPayment.objects.get(pk=payment_id).status, MoveInPayment.STATUS_SUCCESS)

        other_unit = Unit.objects.create(
            property=self.property,
            code="MI-102",
            unit_type="Studio",
            status=Unit.STATUS_VACANT,
        )
        self.unit = other_unit
        mismatch_reservation = self._create_reservation(
            unitId=other_unit.pk,
            tenantEmail="other-move-in-tenant@example.com",
        )
        mismatch_id = mismatch_reservation.json()["settlement"]["id"]
        mismatch_attempt = self.client.post(
            "/api/v1/move-in-settlements/stk-payment",
            data={"action": "create", "settlementId": mismatch_id, "amount": "1000"},
            content_type="application/json",
        )
        self.client.post(
            "/api/v1/move-in-settlements/stk-payment",
            data={
                "action": "attach",
                "paymentId": mismatch_attempt.json()["paymentId"],
                "checkoutRequestId": "ws_CO_MOVEIN_2",
            },
            content_type="application/json",
        )
        mismatch = self.client.post(
            "/api/v1/payments/mpesa/move-in-callback",
            data={
                "checkout_request_id": "ws_CO_MOVEIN_2",
                "result_code": 0,
                "amount": 900,
                "reference": "QWE123457",
            },
            content_type="application/json",
        )
        self.assertEqual(mismatch.status_code, 200, mismatch.content)
        self.assertEqual(mismatch.json()["reason"], "callback_mismatch")
        self.assertEqual(
            MoveInPayment.objects.get(pk=mismatch_attempt.json()["paymentId"]).status,
            MoveInPayment.STATUS_NEEDS_REVIEW,
        )

    def test_tenant_profile_and_onboarding_are_tenant_scoped(self):
        user_model = get_user_model()
        tenant_user = user_model.objects.create_user(
            username="existing-tenant@example.com",
            email="existing-tenant@example.com",
            password="secure-password",
        )
        tenant_user.groups.add(Group.objects.get(name="Tenant (portal)"))
        UserProfile.objects.create(
            user=tenant_user,
            name="Existing Tenant",
            phone="254712340002",
        )
        tenant = Tenant.objects.create(
            branch=self.branch,
            tenant_type=Tenant.INDIVIDUAL,
            name="Existing Tenant",
            national_id_or_passport="ID-EXISTING-101",
            phone="254712340002",
            email="existing-tenant@example.com",
            portal_user=tenant_user,
        )
        tenancy = Tenancy.objects.create(
            unit=self.unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=timezone.localdate(),
            due_day=1,
        )
        self.unit.status = Unit.STATUS_OCCUPIED
        self.unit.save(update_fields=["status"])
        self.client.force_login(tenant_user)
        onboarding = self.client.get("/api/v1/tenant/onboarding")
        self.assertEqual(onboarding.status_code, 200, onboarding.content)
        self.assertFalse(onboarding.json()["tutorialCompleted"])
        completed = self.client.post(
            "/api/v1/tenant/onboarding",
            data={"tutorialCompleted": True},
            content_type="application/json",
        )
        self.assertEqual(completed.status_code, 200)
        tenant_profile = self.client.patch(
            "/api/v1/tenant/profile",
            data={
                "fullName": "Tenant Profile Updated",
                "email": tenant_user.email,
                "phone": "+254 712 340 002",
            },
            content_type="application/json",
        )
        self.assertEqual(tenant_profile.status_code, 200, tenant_profile.content)
        tenant.refresh_from_db()
        self.assertEqual(tenant.name, "Tenant Profile Updated")
        self.assertEqual(tenancy.tenant_id, tenant.pk)


class WhatsAppDeliveryTests(TestCase):
    @override_settings(WHATSAPP_BACKEND="users.whatsapp.ConsoleWhatsAppBackend")
    def test_whatsapp_templates_queue_encrypted_delivery_and_dispatch(self):
        from users.models import WhatsAppDelivery
        from users.whatsapp import (
            dispatch_next_whatsapp_delivery,
            queue_template_whatsapp,
        )

        phone = "254712345631"
        delivery = queue_template_whatsapp(
            phone,
            "payment_receipt",
            {"amount": "24,750.00", "reference": "INV-2026-0117"},
        )

        self.assertEqual(delivery.status, WhatsAppDelivery.STATUS_QUEUED)
        self.assertNotIn(phone.encode(), bytes(delivery.recipient_encrypted))
        self.assertNotIn(b"INV-2026-0117", bytes(delivery.payload_encrypted))
        self.assertTrue(
            AuditLog.objects.filter(
                action="whatsapp.queued",
                entity="WhatsAppDelivery",
            ).exists()
        )

        with patch(
            "users.whatsapp.send_whatsapp",
            return_value="wamid.test-0001",
        ) as send:
            dispatched = dispatch_next_whatsapp_delivery()

        self.assertEqual(dispatched.pk, delivery.pk)
        self.assertEqual(dispatched.status, WhatsAppDelivery.STATUS_SENT)
        self.assertEqual(dispatched.provider_message_id, "wamid.test-0001")
        self.assertEqual(dispatched.recipient_encrypted, b"")
        self.assertEqual(dispatched.payload_encrypted, b"")
        self.assertEqual(send.call_args.args[0], phone)
        payload = send.call_args.args[1]
        self.assertEqual(payload["template"], "payment_confirmation")
        self.assertEqual(
            payload["params"],
            {"amount": "24,750.00", "invoice_ref": "INV-2026-0117"},
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="whatsapp.sent",
                entity="WhatsAppDelivery",
            ).exists()
        )

    @override_settings(WHATSAPP_BACKEND="")
    def test_whatsapp_fan_out_is_silent_when_channel_is_not_configured(self):
        from users.models import WhatsAppDelivery
        from users.whatsapp import queue_template_whatsapp_if_enabled

        result = queue_template_whatsapp_if_enabled(
            "254712345632",
            "payment_receipt",
            {"amount": "100.00", "reference": "REF-1"},
        )

        self.assertIsNone(result)
        self.assertFalse(WhatsAppDelivery.objects.exists())

    @override_settings(
        WHATSAPP_APP_SECRET="test-app-secret",
        WHATSAPP_VERIFY_TOKEN="test-verify-token",
    )
    def test_whatsapp_webhook_verifies_signature_and_records_statuses(self):
        import hashlib
        import hmac as hmac_module

        from users.models import WhatsAppDelivery

        response = self.client.get(
            "/api/v1/whatsapp/webhook",
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "challenge-123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"challenge-123")

        delivery = WhatsAppDelivery.objects.create(
            template_name="payment_receipt",
            recipient_encrypted=b"",
            payload_encrypted=b"",
            status=WhatsAppDelivery.STATUS_SENT,
            provider_message_id="wamid.status-0001",
        )
        body = json.dumps({
            "entry": [{
                "changes": [{
                    "value": {
                        "statuses": [{
                            "id": "wamid.status-0001",
                            "status": "failed",
                            "errors": [{"code": 131042}],
                        }],
                    },
                }],
            }],
        }).encode("utf-8")
        signature = hmac_module.new(
            b"test-app-secret", body, hashlib.sha256
        ).hexdigest()

        rejected = self.client.post(
            "/api/v1/whatsapp/webhook",
            body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )
        self.assertEqual(rejected.status_code, 403)

        accepted = self.client.post(
            "/api/v1/whatsapp/webhook",
            body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": f"sha256={signature}"},
        )
        self.assertEqual(accepted.status_code, 200)
        delivery.refresh_from_db()
        self.assertEqual(delivery.channel_status, "failed")
        self.assertEqual(delivery.error_code, "131042")
        self.assertTrue(
            AuditLog.objects.filter(
                action="whatsapp.account_delivery_blocked",
                entity="WhatsAppDelivery",
            ).exists()
        )

    @override_settings(
        WHATSAPP_APP_SECRET="",
        WHATSAPP_WEBHOOK_TOKEN="cap-token-123",
        DEBUG=False,
    )
    def test_whatsapp_webhook_accepts_capability_token_without_app_secret(self):
        body = json.dumps({"entry": []}).encode("utf-8")

        rejected = self.client.post(
            "/api/v1/whatsapp/webhook", body, content_type="application/json"
        )
        self.assertEqual(rejected.status_code, 403)

        wrong = self.client.post(
            "/api/v1/whatsapp/webhook?token=wrong",
            body,
            content_type="application/json",
        )
        self.assertEqual(wrong.status_code, 403)

        accepted = self.client.post(
            "/api/v1/whatsapp/webhook?token=cap-token-123",
            body,
            content_type="application/json",
        )
        self.assertEqual(accepted.status_code, 200)


@override_settings(WHATSAPP_BACKEND="users.whatsapp.ConsoleWhatsAppBackend")
class WhatsAppBotTests(TestCase):
    def setUp(self):
        self.agency = Agency.objects.create(name="Bot Test Agency")
        self.branch = Branch.objects.create(agency=self.agency, name="Bot Branch")
        self.user_model = get_user_model()

    def _tenant_with_tenancy(self, suffix="01", portal=True):
        portal_user = None
        if portal:
            portal_user = self.user_model.objects.create_user(
                username=f"wa-tenant-{suffix}@example.com",
                email=f"wa-tenant-{suffix}@example.com",
                password="Long-test-passphrase-29!",
            )
        property_obj = Property.objects.create(
            branch=self.branch,
            name=f"WA Property {suffix}",
            property_type="Apartment",
            location="Nairobi",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code=f"WA-{suffix}",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        tenant = Tenant.objects.create(
            branch=self.branch,
            tenant_type=Tenant.INDIVIDUAL,
            name=f"Wanjiku Tester {suffix}",
            national_id_or_passport=f"3041{suffix}99",
            phone=f"2547000011{suffix}",
            portal_user=portal_user,
        )
        tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=timezone.localdate() - timedelta(days=30),
            status=Tenancy.STATUS_ACTIVE,
        )
        return tenant, tenancy

    def _message(self, wa_id, text=None, choice=None, image=None, message_id=None):
        import uuid

        message = {"from": wa_id, "id": message_id or f"wamid.{uuid.uuid4().hex}"}
        if text is not None:
            message["type"] = "text"
            message["text"] = {"body": text}
        elif choice is not None:
            message["type"] = "interactive"
            message["interactive"] = {"list_reply": {"id": choice}}
        elif image is not None:
            message["type"] = "image"
            message["image"] = image
        return message

    def test_verification_links_tenant_and_locks_after_failures(self):
        from users.models import WhatsAppContact
        from users import whatsapp_bot

        tenant, _ = self._tenant_with_tenancy("77")
        wa_id = "254700001177"

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(self._message(wa_id, text="hello"))
        self.assertIn("ID number", post.call_args.args[0]["text"]["body"])

        with patch.object(whatsapp_bot, "_post_message"):
            whatsapp_bot.handle_inbound_message(
                self._message(wa_id, text=tenant.national_id_or_passport)
            )
        contact = WhatsAppContact.objects.get(wa_id=wa_id)
        self.assertIsNotNone(contact.verified_at)
        self.assertEqual(contact.tenant_id, tenant.pk)
        self.assertTrue(
            AuditLog.objects.filter(action="whatsapp.contact_verified").exists()
        )

        other = "254700009999"
        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(self._message(other, text="hi"))
            for _ in range(5):
                whatsapp_bot.handle_inbound_message(
                    self._message(other, text="99999999")
                )
        locked = WhatsAppContact.objects.get(wa_id=other)
        self.assertIsNotNone(locked.locked_until)
        self.assertIn("paused", post.call_args.args[0]["text"]["body"])

    def test_greetings_do_not_count_as_verification_attempts(self):
        from users.models import WhatsAppContact
        from users import whatsapp_bot

        wa_id = "254700008888"
        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(self._message(wa_id, text="hi"))
            for text in ("hello", "Hello there", "hey", "menu", "cancel", "ok"):
                whatsapp_bot.handle_inbound_message(self._message(wa_id, text=text))
        contact = WhatsAppContact.objects.get(wa_id=wa_id)
        self.assertEqual(contact.failed_verifications, 0)
        self.assertIsNone(contact.locked_until)
        self.assertIn("ID number", post.call_args.args[0]["text"]["body"])

    def test_tenant_balance_flow_reports_outstanding_invoices(self):
        from users.models import WhatsAppContact, ConversationState
        from users import whatsapp_bot

        tenant, tenancy = self._tenant_with_tenancy("55")
        invoice = Invoice.objects.create(
            tenancy=tenancy,
            branch=self.branch,
            number="INV-WA-0055",
            period_start=timezone.localdate().replace(day=1),
            period_end=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            total=Decimal("18200.00"),
            status=Invoice.STATUS_ISSUED,
        )
        contact = WhatsAppContact.objects.create(
            wa_id="254700001155",
            tenant=tenant,
            user=tenant.portal_user,
            verified_at=timezone.now(),
        )
        ConversationState.objects.create(contact=contact, active_role="tenant")

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice="t_balance")
            )
        body = post.call_args.args[0]["text"]["body"]
        self.assertIn(invoice.number, body)
        self.assertIn("18,200.00", body)

    def test_photo_maintenance_flow_creates_ticket_with_evidence(self):
        from users.models import WhatsAppContact, ConversationState
        from users import whatsapp_bot

        tenant, tenancy = self._tenant_with_tenancy("33")
        contact = WhatsAppContact.objects.create(
            wa_id="254700001133",
            tenant=tenant,
            user=tenant.portal_user,
            verified_at=timezone.now(),
        )
        ConversationState.objects.create(contact=contact, active_role="tenant")

        with patch.object(whatsapp_bot, "_post_message"):
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice="t_issue")
            )
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice="cat_plumbing")
            )
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, text="Kitchen tap is leaking badly")
            )
            with patch.object(
                whatsapp_bot,
                "_fetch_media",
                return_value=(b"fake-jpeg-bytes", "image/jpeg"),
            ), patch.object(whatsapp_bot, "upload_private_object") as upload:
                whatsapp_bot.handle_inbound_message(
                    self._message(
                        contact.wa_id,
                        image={"id": "media-1", "sha256": "abc", "mime_type": "image/jpeg"},
                    )
                )
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, text="done")
            )

        ticket = MaintenanceTicket.objects.get(unit=tenancy.unit)
        self.assertEqual(ticket.category, MaintenanceTicket.CATEGORY_PLUMBING)
        self.assertEqual(ticket.description, "Kitchen tap is leaking badly")
        self.assertEqual(ticket.reported_by, tenant.portal_user)
        self.assertEqual(len(ticket.photos), 1)
        self.assertEqual(ticket.photos[0]["content_type"], "image/jpeg")
        self.assertTrue(
            ticket.photos[0]["storage_key"].startswith(f"maintenance/tenant/{tenant.pk}/")
        )
        upload.assert_called_once()
        self.assertTrue(
            AuditLog.objects.filter(
                action="maintenance_ticket.tenant_created",
                new_values__source="whatsapp",
            ).exists()
        )

    def test_cancel_abandons_draft_ticket_and_returns_to_menu(self):
        from users.models import WhatsAppContact, ConversationState
        from users import whatsapp_bot

        tenant, tenancy = self._tenant_with_tenancy("44")
        contact = WhatsAppContact.objects.create(
            wa_id="254700001144",
            tenant=tenant,
            user=tenant.portal_user,
            verified_at=timezone.now(),
        )
        state = ConversationState.objects.create(contact=contact, active_role="tenant")

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice="t_issue")
            )
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice="cat_plumbing")
            )
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, text="Cancel")
            )

        state.refresh_from_db()
        self.assertEqual(state.state.get("flow"), "menu")
        self.assertFalse(MaintenanceTicket.objects.filter(unit=tenancy.unit).exists())
        self.assertEqual(post.call_args.args[0]["type"], "interactive")

    def test_caretaker_completes_ticket_and_tenant_is_notified(self):
        from users.models import WhatsAppContact, ConversationState, WhatsAppDelivery
        from users import whatsapp_bot

        tenant, tenancy = self._tenant_with_tenancy("66")
        caretaker = self.user_model.objects.create_user(
            username="wa-caretaker@example.com",
            email="wa-caretaker@example.com",
            password="Long-test-passphrase-29!",
        )
        caretaker.groups.add(Group.objects.get_or_create(name="Caretaker")[0])
        tenancy.unit.property.caretakers.add(caretaker)
        ticket = MaintenanceTicket.objects.create(
            property=tenancy.unit.property,
            unit=tenancy.unit,
            reported_by=tenant.portal_user,
            title="Broken lock",
            description="Broken lock",
            category=MaintenanceTicket.CATEGORY_SECURITY,
            priority=MaintenanceTicket.PRIORITY_HIGH,
        )
        contact = WhatsAppContact.objects.create(
            wa_id="254700001166",
            user=caretaker,
            verified_at=timezone.now(),
        )
        ConversationState.objects.create(contact=contact, active_role="caretaker")

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice="c_tickets")
            )
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice=f"tkt_{ticket.pk}")
            )
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice=f"tc_{ticket.pk}")
            )

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, MaintenanceTicket.STATUS_COMPLETED)
        self.assertTrue(
            WhatsAppDelivery.objects.filter(
                template_name="maintenance_ticket_completed_tenant"
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="maintenance_ticket.caretaker_completed"
            ).exists()
        )
        bodies = [c.args[0] for c in post.call_args_list]
        self.assertIn("marked complete", bodies[-1]["text"]["body"])

    def test_switch_role_and_session_expiry_reset_to_entry(self):
        from users.models import WhatsAppContact, ConversationState
        from users import whatsapp_bot

        tenant, _ = self._tenant_with_tenancy("88")
        caretaker_group = Group.objects.get_or_create(name="Caretaker")[0]
        tenant.portal_user.groups.add(caretaker_group)
        other_property = Property.objects.create(
            branch=self.branch,
            name="Switch Test Property",
            property_type="Apartment",
            location="Nakuru",
        )
        other_property.caretakers.add(tenant.portal_user)
        contact = WhatsAppContact.objects.create(
            wa_id="254700001188",
            tenant=tenant,
            user=tenant.portal_user,
            verified_at=timezone.now(),
        )
        state = ConversationState.objects.create(contact=contact, active_role="tenant")

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, text="switch")
            )
        buttons = post.call_args.args[0]["interactive"]["action"]["buttons"]
        self.assertEqual(
            [b["reply"]["id"] for b in buttons], ["role_tenant", "role_caretaker"]
        )

        with patch.object(whatsapp_bot, "_post_message"):
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, choice="role_caretaker")
            )
        state.refresh_from_db()
        self.assertEqual(state.active_role, "caretaker")

        state.expires_at = timezone.now() - timedelta(minutes=1)
        state.save(update_fields=["expires_at"])
        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(
                self._message(contact.wa_id, text="hello")
            )
        state.refresh_from_db()
        self.assertEqual(state.state.get("flow"), "choose_role")
        buttons = post.call_args.args[0]["interactive"]["action"]["buttons"]
        self.assertEqual(len(buttons), 2)


@override_settings(
    WHATSAPP_BACKEND="users.whatsapp.ConsoleWhatsAppBackend",
    AUTH_SMS_BACKEND="users.sms.ConsoleSMSBackend",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE="522533",
    MPESA_PASSKEY="test-passkey",
    PUBLIC_SITE_URL="https://staging.legacy.example",
)
class WhatsAppRentStkTests(TestCase):
    def setUp(self):
        self.agency = Agency.objects.create(name="STK Test Agency")
        self.branch = Branch.objects.create(agency=self.agency, name="STK Branch")
        self.user_model = get_user_model()
        self.portal_user = self.user_model.objects.create_user(
            username="stk-tenant@example.com",
            email="stk-tenant@example.com",
            password="Long-test-passphrase-29!",
        )
        property_obj = Property.objects.create(
            branch=self.branch,
            name="STK Property",
            property_type="Apartment",
            location="Nairobi",
        )
        unit = Unit.objects.create(
            property=property_obj,
            code="STK-1",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        self.tenant = Tenant.objects.create(
            branch=self.branch,
            tenant_type=Tenant.INDIVIDUAL,
            name="Stk Tester",
            national_id_or_passport="30415599",
            phone="254700002211",
            email="stk-tenant@example.com",
            portal_user=self.portal_user,
        )
        self.tenancy = Tenancy.objects.create(
            unit=unit,
            tenant=self.tenant,
            tenancy_type=Tenancy.TYPE_MONTH_TO_MONTH,
            start_date=timezone.localdate() - timedelta(days=30),
            status=Tenancy.STATUS_ACTIVE,
        )
        self.invoice = Invoice.objects.create(
            tenancy=self.tenancy,
            branch=self.branch,
            number="INV-STK-0001",
            period_start=timezone.localdate().replace(day=1),
            period_end=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=5),
            total=Decimal("20000.00"),
            status=Invoice.STATUS_ISSUED,
        )
        from users.models import WhatsAppContact, ConversationState

        self.contact = WhatsAppContact.objects.create(
            wa_id="254700002211",
            tenant=self.tenant,
            user=self.portal_user,
            verified_at=timezone.now(),
        )
        ConversationState.objects.create(contact=self.contact, active_role="tenant")

    def _message(self, choice=None, text=None):
        import uuid

        message = {"from": self.contact.wa_id, "id": f"wamid.{uuid.uuid4().hex}"}
        if choice is not None:
            message["type"] = "interactive"
            message["interactive"] = {"list_reply": {"id": choice}}
        else:
            message["type"] = "text"
            message["text"] = {"body": text}
        return message

    def test_pay_flow_initiates_stk_and_callback_completes_tri_channel_receipt(self):
        from users.models import (
            RentStkRequest,
            SMSDelivery,
            WhatsAppDelivery,
        )
        from users import whatsapp_bot

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(self._message(choice="t_pay"))
            whatsapp_bot.handle_inbound_message(
                self._message(choice=f"inv_{self.invoice.pk}")
            )
            with patch.object(
                whatsapp_bot.daraja,
                "stk_push",
                return_value={
                    "checkout_request_id": "ws_CO_TEST_001",
                    "merchant_request_id": "mr_001",
                },
            ) as stk:
                whatsapp_bot.handle_inbound_message(
                    self._message(choice=f"stk_{self.invoice.pk}")
                )

        stk.assert_called_once()
        request_row = RentStkRequest.objects.get(checkout_request_id="ws_CO_TEST_001")
        self.assertEqual(request_row.invoice, self.invoice)
        self.assertEqual(request_row.status, RentStkRequest.STATUS_PENDING)
        self.assertIn("M-PESA prompt", post.call_args.args[0]["text"]["body"])

        callback = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "mr_001",
                    "CheckoutRequestID": "ws_CO_TEST_001",
                    "ResultCode": 0,
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 20000.00},
                            {"Name": "MpesaReceiptNumber", "Value": "SHK31XYZ9A"},
                            {"Name": "PhoneNumber", "Value": 254700002211},
                        ],
                    },
                },
            },
        }
        with patch.object(whatsapp_bot, "_send_payment_receipt_email") as email:
            response = self.client.post(
                "/api/v1/payments/mpesa/rent-stk-callback",
                json.dumps(callback),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)

        request_row.refresh_from_db()
        self.assertEqual(request_row.status, RentStkRequest.STATUS_SUCCEEDED)
        payment = Payment.objects.get(reference="SHK31XYZ9A")
        self.assertEqual(payment.tenant, self.tenant)
        self.assertEqual(payment.amount, Decimal("20000.00"))
        self.assertEqual(payment.source, Payment.SOURCE_MPESA)
        self.assertEqual(payment.allocations.get().invoice, self.invoice)
        self.assertTrue(Receipt.objects.filter(payment=payment).exists())
        # Tri-channel receipt: WhatsApp template + SMS queued, email sent
        self.assertTrue(
            WhatsAppDelivery.objects.filter(template_name="payment_receipt").exists()
        )
        self.assertTrue(
            SMSDelivery.objects.filter(template_name="payment_receipt").exists()
        )
        email.assert_called_once()

        # Replaying the same callback must not double-allocate
        self.client.post(
            "/api/v1/payments/mpesa/rent-stk-callback",
            json.dumps(callback),
            content_type="application/json",
        )
        self.assertEqual(Payment.objects.filter(reference="SHK31XYZ9A").count(), 1)

    def test_failed_stk_callback_marks_request_and_notifies(self):
        from users.models import RentStkRequest
        from users import whatsapp_bot

        RentStkRequest.objects.create(
            invoice=self.invoice,
            tenant=self.tenant,
            contact=self.contact,
            amount=Decimal("20000.00"),
            phone=self.contact.wa_id,
            checkout_request_id="ws_CO_FAIL_01",
        )
        callback = {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": "ws_CO_FAIL_01",
                    "ResultCode": 1032,
                    "ResultDesc": "Request cancelled by user",
                },
            },
        }
        with patch.object(whatsapp_bot, "_post_message") as post:
            response = self.client.post(
                "/api/v1/payments/mpesa/rent-stk-callback",
                json.dumps(callback),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        row = RentStkRequest.objects.get(checkout_request_id="ws_CO_FAIL_01")
        self.assertEqual(row.status, RentStkRequest.STATUS_FAILED)
        self.assertIn("not completed", post.call_args.args[0]["text"]["body"])
        self.assertFalse(Payment.objects.exists())


@override_settings(WHATSAPP_BACKEND="users.whatsapp.ConsoleWhatsAppBackend")
class WhatsAppLandlordTests(TestCase):
    def setUp(self):
        self.agency = Agency.objects.create(name="LL Test Agency")
        self.branch = Branch.objects.create(agency=self.agency, name="LL Branch")
        self.user_model = get_user_model()
        self.portal_user = self.user_model.objects.create_user(
            username="wa-landlord@example.com",
            email="wa-landlord@example.com",
            password="Long-test-passphrase-29!",
        )
        self.landlord = Landlord.objects.create(
            branch=self.branch,
            portal_user=self.portal_user,
            name="Wanjiku Landlady",
            id_or_registration_number="LL-889900",
            phone="254700003322",
        )
        self.property = Property.objects.create(
            branch=self.branch,
            landlord=self.landlord,
            name="LL Property",
            property_type="Apartment",
            location="Nairobi",
        )
        self.unit = Unit.objects.create(
            property=self.property,
            code="LL-1",
            unit_type="Studio",
            status=Unit.STATUS_OCCUPIED,
        )
        self.ticket = MaintenanceTicket.objects.create(
            property=self.property,
            unit=self.unit,
            reported_by=self.portal_user,
            title="Roof leak",
            description="Roof leak",
            category=MaintenanceTicket.CATEGORY_STRUCTURAL,
            priority=MaintenanceTicket.PRIORITY_HIGH,
        )
        self.vendor = Vendor.objects.create(
            branch=self.branch,
            name="FixIt Ltd",
            trade="Roofing",
            phone="254700004433",
        )
        self.quotation = MaintenanceQuotation.objects.create(
            ticket=self.ticket,
            vendor=self.vendor,
            submitted_by=self.portal_user,
            amount=Decimal("45000.00"),
            status=MaintenanceQuotation.STATUS_SUBMITTED,
        )
        from users.models import WhatsAppContact, ConversationState

        self.contact = WhatsAppContact.objects.create(
            wa_id="254700003322",
            user=self.portal_user,
            verified_at=timezone.now(),
        )
        ConversationState.objects.create(contact=self.contact, active_role="landlord")

    def _choice(self, choice):
        import uuid

        return {
            "from": self.contact.wa_id,
            "id": f"wamid.{uuid.uuid4().hex}",
            "type": "interactive",
            "interactive": {"button_reply": {"id": choice}},
        }

    def test_landlord_sees_and_approves_pending_quotation(self):
        from users import whatsapp_bot

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(self._choice("l_approvals"))
        body = post.call_args.args[0]["interactive"]["body"]["text"]
        self.assertIn("FixIt Ltd", body)
        self.assertIn("45,000.00", body)

        with patch.object(whatsapp_bot, "_post_message") as post:
            whatsapp_bot.handle_inbound_message(
                self._choice(f"q_app_{self.quotation.pk}")
            )
        self.quotation.refresh_from_db()
        self.assertEqual(self.quotation.status, MaintenanceQuotation.STATUS_APPROVED)
        self.assertEqual(self.quotation.approved_by, self.portal_user)
        self.assertIn("approved", post.call_args.args[0]["text"]["body"])
        self.assertTrue(
            AuditLog.objects.filter(
                action="maintenance_quotation.landlord_decided",
                new_values__approved=True,
            ).exists()
        )
