"""Seed an idempotent demo dataset for exercising the WhatsApp integration.

Creates one agency/branch, a property with an occupied unit, a tenant with a
known national ID and WhatsApp phone, an active tenancy with an issued
invoice, and a landlord with a submitted maintenance quotation — everything
the conversational flows need. Safe to re-run; existing rows are reused.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from users.models import (
    Agency,
    Branch,
    Invoice,
    Landlord,
    MaintenanceQuotation,
    MaintenanceTicket,
    Property,
    Tenancy,
    Tenant,
    Unit,
    UserProfile,
    Vendor,
    WhatsAppContact,
)
from users.roles import (
    BRANCH_MANAGER_ROLE,
    CARETAKER_ROLE,
    LANDLORD_PORTAL_ROLE,
    SUPER_ADMIN_ROLE,
    TENANT_PORTAL_ROLE,
)

STAFF_ACCOUNTS = (
    ("demo-director@legacyhomes.live", "Demo Director", SUPER_ADMIN_ROLE, "254700000010"),
    ("demo-manager@legacyhomes.live", "Demo Branch Manager", BRANCH_MANAGER_ROLE, "254700000011"),
    ("demo-accountant@legacyhomes.live", "Demo Accountant", "Accountant", "254700000012"),
    ("demo-officer@legacyhomes.live", "Demo Property Officer", "Property Officer / Agent", "254700000013"),
    ("demo-caretaker@legacyhomes.live", "Demo Caretaker", CARETAKER_ROLE, "254700000014"),
)

# (email, name, national id, phone, unit code, invoice number, invoice total)
EXTRA_TENANTS = (
    ("kimani.demo@legacyhomes.live", "Kimani Kimani", "20456721", "254704173241",
     "B5", "INV-DEMO-0002", "22000.00"),
    ("brian.demo@legacyhomes.live", "Brian Demo", "31782245", "254790541516",
     "B6", "INV-DEMO-0003", "15500.00"),
    ("pekim.demo@legacyhomes.live", "Pekim Demo", "28901134", "254797933409",
     "B7", "INV-DEMO-0004", "30000.00"),
)


class Command(BaseCommand):
    help = "Create demo data for testing the WhatsApp flows (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-phone", default="254716060198")
        parser.add_argument("--tenant-id-number", default="30415599")
        parser.add_argument("--landlord-phone", default="254725571239")
        parser.add_argument("--landlord-id-number", default="LL-889900")
        parser.add_argument(
            "--portal-password",
            default="",
            help="If set, both portal users get this password; otherwise "
            "passwords are left unusable (WhatsApp flows don't need them).",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        today = timezone.localdate()

        agency, _ = Agency.objects.get_or_create(name="Legacy Real Estate")
        branch, _ = Branch.objects.get_or_create(agency=agency, name="Nairobi")

        tenant_group, _ = Group.objects.get_or_create(name=TENANT_PORTAL_ROLE)
        landlord_group, _ = Group.objects.get_or_create(name=LANDLORD_PORTAL_ROLE)

        def portal_user(email, group):
            user, created = user_model.objects.get_or_create(
                username=email,
                defaults={"email": email},
            )
            if options["portal_password"]:
                user.set_password(options["portal_password"])
                user.save(update_fields=["password"])
            elif created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
            user.groups.add(group)
            return user

        landlord_user = portal_user("demo-landlord@legacyhomes.live", landlord_group)
        landlord, _ = Landlord.objects.get_or_create(
            branch=branch,
            id_or_registration_number=options["landlord_id_number"],
            defaults={
                "name": "Demo Landlord",
                "phone": options["landlord_phone"],
                "portal_user": landlord_user,
            },
        )

        property_obj, _ = Property.objects.get_or_create(
            branch=branch,
            name="Legacy Court",
            defaults={
                "landlord": landlord,
                "property_type": "Apartment",
                "location": "Nairobi",
            },
        )
        unit, _ = Unit.objects.get_or_create(
            property=property_obj,
            code="B4",
            defaults={"unit_type": "2 Bedroom", "status": Unit.STATUS_OCCUPIED},
        )

        tenant_user = portal_user("demo-tenant@legacyhomes.live", tenant_group)
        tenant, _ = Tenant.objects.get_or_create(
            branch=branch,
            national_id_or_passport=options["tenant_id_number"],
            defaults={
                "tenant_type": Tenant.INDIVIDUAL,
                "name": "Jane Demo",
                "phone": options["tenant_phone"],
                "email": "demo-tenant@legacyhomes.live",
                "portal_user": tenant_user,
            },
        )
        tenancy, _ = Tenancy.objects.get_or_create(
            unit=unit,
            tenant=tenant,
            defaults={
                "tenancy_type": Tenancy.TYPE_MONTH_TO_MONTH,
                "start_date": today - timedelta(days=60),
                "status": Tenancy.STATUS_ACTIVE,
            },
        )

        invoice, _ = Invoice.objects.get_or_create(
            number="INV-DEMO-0001",
            defaults={
                "tenancy": tenancy,
                "branch": branch,
                "period_start": today.replace(day=1),
                "period_end": today,
                "due_date": today + timedelta(days=5),
                "total": Decimal("18200.00"),
                "status": Invoice.STATUS_ISSUED,
            },
        )

        ticket, _ = MaintenanceTicket.objects.get_or_create(
            property=property_obj,
            unit=unit,
            title="Demo: kitchen tap leaking",
            defaults={
                "reported_by": tenant_user,
                "description": "Demo: kitchen tap leaking",
                "category": MaintenanceTicket.CATEGORY_PLUMBING,
                "priority": MaintenanceTicket.PRIORITY_NORMAL,
            },
        )
        vendor, _ = Vendor.objects.get_or_create(
            branch=branch,
            name="FixIt Demo Ltd",
            defaults={"trade": "Plumbing", "phone": "254700000001"},
        )
        MaintenanceQuotation.objects.get_or_create(
            ticket=ticket,
            vendor=vendor,
            defaults={
                "submitted_by": landlord_user,
                "amount": Decimal("12500.00"),
                "status": MaintenanceQuotation.STATUS_SUBMITTED,
            },
        )

        for email, name, role_name, phone in STAFF_ACCOUNTS:
            group, _ = Group.objects.get_or_create(name=role_name)
            user = portal_user(email, group)
            profile, _ = UserProfile.objects.get_or_create(
                user=user,
                defaults={"name": name, "phone": phone},
            )
            profile.branches.add(branch)
            if role_name == CARETAKER_ROLE:
                property_obj.caretakers.add(user)
                if not profile.id_or_registration_number:
                    profile.id_or_registration_number = "CT-445566"
                    profile.save(update_fields=["id_or_registration_number"])

        tester_lines = []
        for email, name, id_number, phone, unit_code, invoice_number, total in EXTRA_TENANTS:
            user = portal_user(email, tenant_group)
            extra_unit, _ = Unit.objects.get_or_create(
                property=property_obj,
                code=unit_code,
                defaults={"unit_type": "1 Bedroom", "status": Unit.STATUS_OCCUPIED},
            )
            extra_tenant, _ = Tenant.objects.get_or_create(
                branch=branch,
                national_id_or_passport=id_number,
                defaults={
                    "tenant_type": Tenant.INDIVIDUAL,
                    "name": name,
                    "phone": phone,
                    "email": email,
                    "portal_user": user,
                },
            )
            extra_tenancy, _ = Tenancy.objects.get_or_create(
                unit=extra_unit,
                tenant=extra_tenant,
                defaults={
                    "tenancy_type": Tenancy.TYPE_MONTH_TO_MONTH,
                    "start_date": today - timedelta(days=90),
                    "status": Tenancy.STATUS_ACTIVE,
                },
            )
            Invoice.objects.get_or_create(
                number=invoice_number,
                defaults={
                    "tenancy": extra_tenancy,
                    "branch": branch,
                    "period_start": today.replace(day=1),
                    "period_end": today,
                    "due_date": today + timedelta(days=5),
                    "total": Decimal(total),
                    "status": Invoice.STATUS_ISSUED,
                },
            )
            tester_lines.append(
                f"  Tester: {name} — ID {id_number}, phone {phone}, "
                f"unit {unit_code}, {invoice_number} KES {total}, portal {email}"
            )

        # Kimani is also a landlord, exercising the multi-role chooser.
        kimani_user = user_model.objects.get(username="kimani.demo@legacyhomes.live")
        kimani_user.groups.add(landlord_group)
        kimani_landlord, _ = Landlord.objects.get_or_create(
            branch=branch,
            id_or_registration_number="KIM-556677",
            defaults={
                "name": "Kimani Kimani",
                "phone": "254704173241",
                "portal_user": kimani_user,
            },
        )
        villas, _ = Property.objects.get_or_create(
            branch=branch,
            name="Legacy Villas",
            defaults={
                "landlord": kimani_landlord,
                "property_type": "Apartment",
                "location": "Nakuru",
            },
        )
        villas_unit, _ = Unit.objects.get_or_create(
            property=villas,
            code="V1",
            defaults={"unit_type": "3 Bedroom", "status": Unit.STATUS_VACANT},
        )

        # Jane (primary demo tenant) doubles as caretaker of Legacy Villas —
        # a property where she is NOT a tenant — to exercise the
        # tenant/caretaker role chooser and the ticket completion flow.
        caretaker_group, _ = Group.objects.get_or_create(name=CARETAKER_ROLE)
        tenant_user.groups.add(caretaker_group)
        villas.caretakers.add(tenant_user)
        officer_user = user_model.objects.get(username="demo-officer@legacyhomes.live")
        MaintenanceTicket.objects.get_or_create(
            property=villas,
            unit=villas_unit,
            title="Demo: gate light not working",
            defaults={
                "reported_by": officer_user,
                "description": "Demo: gate light not working",
                "category": MaintenanceTicket.CATEGORY_ELECTRICAL,
                "priority": MaintenanceTicket.PRIORITY_NORMAL,
            },
        )

        # The demo landlord is also a tenant (unit B8) and a caretaker of
        # Legacy Court, demonstrating all three WhatsApp roles on one number.
        landlord_user.groups.add(tenant_group)
        landlord_user.groups.add(caretaker_group)
        property_obj.caretakers.add(landlord_user)
        ll_unit, _ = Unit.objects.get_or_create(
            property=property_obj,
            code="B8",
            defaults={"unit_type": "1 Bedroom", "status": Unit.STATUS_OCCUPIED},
        )
        ll_tenant, _ = Tenant.objects.get_or_create(
            branch=branch,
            national_id_or_passport="27334455",
            defaults={
                "tenant_type": Tenant.INDIVIDUAL,
                "name": "Demo Landlord",
                "phone": options["landlord_phone"],
                "email": "demo-landlord@legacyhomes.live",
                "portal_user": landlord_user,
            },
        )
        ll_tenancy, _ = Tenancy.objects.get_or_create(
            unit=ll_unit,
            tenant=ll_tenant,
            defaults={
                "tenancy_type": Tenancy.TYPE_MONTH_TO_MONTH,
                "start_date": today - timedelta(days=45),
                "status": Tenancy.STATUS_ACTIVE,
            },
        )
        Invoice.objects.get_or_create(
            number="INV-DEMO-0005",
            defaults={
                "tenancy": ll_tenancy,
                "branch": branch,
                "period_start": today.replace(day=1),
                "period_end": today,
                "due_date": today + timedelta(days=5),
                "total": Decimal("12000.00"),
                "status": Invoice.STATUS_ISSUED,
            },
        )

        # Re-running the seed always unblocks the demo phones, so a failed
        # verification experiment never stalls staging testing for 24 hours.
        demo_phones = [options["tenant_phone"], options["landlord_phone"]] + [
            row[3] for row in EXTRA_TENANTS
        ]
        unlocked = WhatsAppContact.objects.filter(wa_id__in=demo_phones).update(
            failed_verifications=0, locked_until=None
        )
        if unlocked:
            self.stdout.write(f"  Cleared verification locks on {unlocked} demo contact(s).")
        for line in tester_lines:
            self.stdout.write(line)

        self.stdout.write(self.style.SUCCESS(
            "Demo data ready:\n"
            f"  Tenant: {tenant.name} — ID {tenant.national_id_or_passport}, "
            f"phone {tenant.phone}, portal demo-tenant@legacyhomes.live\n"
            f"  Invoice: {invoice.number} KES {invoice.total} ({invoice.status})\n"
            f"  Landlord: {landlord.name} — ID {landlord.id_or_registration_number}, "
            f"phone {landlord.phone}, portal demo-landlord@legacyhomes.live\n"
            f"  Quotation pending approval on ticket #{ticket.pk}\n"
            "  Caretaker WhatsApp ID: CT-445566 (demo-caretaker)\n"
            f"  Staff: " + ", ".join(email for email, *_ in STAFF_ACCOUNTS)
        ))
