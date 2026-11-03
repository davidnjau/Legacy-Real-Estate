import re
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
from django.utils import timezone
from users.admin import AuditedGroupAdmin, AuditLogAdmin, BranchAdmin
from users.audit import record_audit_event
from users.models import (
    ArrearsPaymentPlan,
    ArrearsPolicy,
    AuditLog,
    BankStatementImport,
    BankStatementRow,
    Block,
    Branch,
    DepositAccount,
    DepositEntry,
    Floor,
    Invoice,
    Landlord,
    ManagementAgreement,
    MeterReading,
    MoveInCharge,
    MoveInChecklistItem,
    MoveInPackage,
    MpesaCallback,
    Payment,
    PaymentAllocation,
    Property,
    PropertyDocument,
    Receipt,
    SMSLoginChallenge,
    Tenancy,
    Tenant,
    TenantActivationChallenge,
    TenantCredit,
    TenantDocument,
    Unit,
    UnitCharge,
    UnitReservation,
    UserProfile,
    UtilityConfiguration,
)
from users.roles import SUPER_ADMIN_ROLE
from users.sms import send_sms


class HealthCheckTests(SimpleTestCase):
    def test_health_endpoint_returns_ok(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_endpoint_is_public_and_read_only(self):
        response = self.client.post("/api/v1/health")

        self.assertEqual(response.status_code, 405)


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
            previous_value=Decimal("100"),
            current_value=Decimal("110"),
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
            previous_value=Decimal("110"),
            current_value=Decimal("111"),
            submitted_by=self.create_user(phone="254712345671"),
        )
        call_command("generate_monthly_invoices", period="2026-03-01", stdout=output)
        march = Invoice.objects.get(tenancy=tenancy, period_start=date(2026, 3, 1))
        self.assertEqual(march.lines.get(charge_type="balance").amount, Decimal("30855.00"))
        self.assertEqual(march.total, Decimal("61155.00"))

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
            f"/api/v1/payments/daily-reconciliation?from={received_at.date().isoformat()}&to={received_at.date().isoformat()}"
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
        self.assertEqual(unit.status, Unit.STATUS_OCCUPIED)
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
        challenge = TenantActivationChallenge.objects.get(tenant=tenant)
        self.assertNotEqual(challenge.token_hash, token)

        activated = self.client.post(
            "/api/v1/tenant-activation/complete",
            data={"token": token, "password": "a-secure-password"},
            content_type="application/json",
        )
        self.assertEqual(activated.status_code, 200)
        tenant.refresh_from_db()
        self.assertIsNotNone(tenant.portal_user_id)
        self.assertTrue(tenant.portal_user.check_password("a-secure-password"))
        self.assertEqual(
            self.client.post(
                "/api/v1/tenant-activation/complete",
                data={"token": token, "password": "another-password"},
                content_type="application/json",
            ).status_code,
            400,
        )

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

        document = self.client.post(
            f"/api/v1/tenants/{tenant.pk}/documents",
            data={
                "document_type": "national_id",
                "name": "Amina ID",
                "storage_key": "tenants/1/national-id.pdf",
                "metadata": {"content_type": "application/pdf"},
            },
            content_type="application/json",
        )
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
            "users.admin_authentication.send_sms",
            side_effect=lambda phone, message: delivered.append(message),
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

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE users_auditlog SET action = %s WHERE id = %s",
                        ["changed", event.pk],
                    )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
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
        branch = Branch(name="Kisumu", town="Kisumu")

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

    @override_settings(AUTH_SMS_BACKEND="test_backend.SMS")
    def test_finance_roles_require_sms_verification(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        delivered = []

        with patch("users.views.send_sms", side_effect=lambda phone, message: delivered.append(message)):
            response = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["mfa_required"])
        self.assertNotIn("code", response.json())
        self.assertEqual(SMSLoginChallenge.objects.count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.mfa_challenge_issued",
            ).exists()
        )
        code = re.search(r"\b(\d{6})\b", delivered[0]).group(1)

        challenge_id = response.json()["challenge_id"]
        for malformed_payload in (
            {"challenge_id": challenge_id, "code": "123"},
            {"challenge_id": challenge_id, "code": "١٢٣٤٥٦"},
            {"challenge_id": True, "code": code},
        ):
            malformed_code = self.client.post(
                "/api/v1/auth/mfa/verify",
                data=malformed_payload,
                content_type="application/json",
            )
            self.assertEqual(malformed_code.status_code, 400)
        self.assertEqual(SMSLoginChallenge.objects.get().attempts, 0)

        invalid_code = "000000" if code != "000000" else "000001"
        rejected = self.client.post(
            "/api/v1/auth/mfa/verify",
            data={
                "challenge_id": response.json()["challenge_id"],
                "code": invalid_code,
            },
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(SMSLoginChallenge.objects.get().attempts, 1)

        verified = self.client.post(
            "/api/v1/auth/mfa/verify",
            data={"challenge_id": response.json()["challenge_id"], "code": code},
            content_type="application/json",
        )

        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["user"]["id"], user.pk)
        self.assertFalse(SMSLoginChallenge.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                user=user,
                action="login.succeeded",
            ).exists()
        )

    @override_settings(AUTH_SMS_MAX_ATTEMPTS=1)
    def test_final_invalid_sms_code_consumes_challenge(self):
        user = self.create_user()
        user.groups.add(Group.objects.get(name="Accountant"))
        with patch("users.views.send_sms"):
            challenge_response = self.client.post(
                "/api/v1/auth/login",
                data={"identifier": user.email, "password": "Long-test-passphrase-29!"},
                content_type="application/json",
            )
        challenge_id = challenge_response.json()["challenge_id"]

        rejected = self.client.post(
            "/api/v1/auth/mfa/verify",
            data={"challenge_id": challenge_id, "code": "000000"},
            content_type="application/json",
        )

        self.assertEqual(rejected.status_code, 401)
        self.assertFalse(SMSLoginChallenge.objects.filter(pk=challenge_id).exists())
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

    def test_sms_authentication_fails_closed_without_a_production_provider(self):
        with override_settings(DEBUG=False, AUTH_SMS_BACKEND=""):
            with self.assertRaises(ImproperlyConfigured):
                send_sms("254712345678", "test")
