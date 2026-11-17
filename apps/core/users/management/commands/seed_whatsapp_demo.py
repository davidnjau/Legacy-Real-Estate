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
    Vendor,
)
from users.roles import LANDLORD_PORTAL_ROLE, TENANT_PORTAL_ROLE


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

        self.stdout.write(self.style.SUCCESS(
            "Demo data ready:\n"
            f"  Tenant: {tenant.name} — ID {tenant.national_id_or_passport}, "
            f"phone {tenant.phone}, portal demo-tenant@legacyhomes.live\n"
            f"  Invoice: {invoice.number} KES {invoice.total} ({invoice.status})\n"
            f"  Landlord: {landlord.name} — ID {landlord.id_or_registration_number}, "
            f"phone {landlord.phone}, portal demo-landlord@legacyhomes.live\n"
            f"  Quotation pending approval on ticket #{ticket.pk}"
        ))
