import csv
import logging
import re
import secrets
import smtplib
from zipfile import BadZipFile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from users.models import (
    Block,
    DepositAccount,
    DepositEntry,
    Floor,
    Invoice,
    InvoiceLine,
    MeterReading,
    Payment,
    PaymentAllocation,
    AgencyMembership,
    Property,
    PropertyUnitType,
    PropertyUnitTypeUtility,
    Tenant,
    TenantActivationChallenge,
    Tenancy,
    Unit,
    UnitCharge,
    UtilityConfiguration,
    UserProfile,
)
from users.roles import TENANT_PORTAL_ROLE

logger = logging.getLogger(__name__)

SHEET_NAMES = {
    "units",
    "tenancies",
    "properties",
    "units_tenants",
    "charges",
    "opening_arrears",
    "payments",
    "meter_readings",
    "deposits",
}
MAX_FILE_BYTES = 10 * 1024 * 1024


def _canonical_sheet_key(value):
    sheet = _sheet_key(value)
    return {
        "unit": "units",
        "tenancy": "tenancies",
    }.get(sheet, sheet)


class ImportProblem(Exception):
    def __init__(self, sheet, row, message):
        self.issue = {"sheet": sheet, "row": row, "message": message}
        super().__init__(message)


class PreviewComplete(Exception):
    def __init__(self, report):
        self.report = report


def _sheet_key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")


def _cell(value):
    return value.strip() if isinstance(value, str) else value


def _csv_sheet(file_name, content):
    sheet = _canonical_sheet_key(file_name.rsplit("/", 1)[-1].rsplit(".", 1)[0])
    if sheet not in SHEET_NAMES:
        raise ImportProblem(sheet or "file", 1, "The CSV filename must match a supported sheet name.")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportProblem(sheet, 1, "CSV files must use UTF-8 encoding.") from exc
    reader = csv.reader(StringIO(decoded))
    rows = list(reader)
    if not rows:
        return sheet, []
    return sheet, rows


def _parse_files(files):
    parsed = {}
    total_bytes = 0
    for uploaded in files:
        content = uploaded.read()
        total_bytes += len(content)
        if total_bytes > MAX_FILE_BYTES:
            raise ImportProblem("file", 1, "The combined upload size cannot exceed 10 MB.")
        name = uploaded.name.casefold()
        if name.endswith(".xlsx"):
            try:
                workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            except (InvalidFileException, BadZipFile, OSError, ValueError) as exc:
                raise ImportProblem("workbook", 1, "The XLSX workbook could not be read.") from exc
            for worksheet in workbook.worksheets:
                sheet = _canonical_sheet_key(worksheet.title)
                if sheet not in SHEET_NAMES:
                    raise ImportProblem(worksheet.title, 1, "This worksheet name is not supported.")
                rows = list(worksheet.iter_rows(values_only=True))
                if rows:
                    if sheet in parsed:
                        raise ImportProblem(worksheet.title, 1, "Worksheet names must be unique after normalization.")
                    parsed[sheet] = rows
            workbook.close()
        elif name.endswith(".csv"):
            sheet, rows = _csv_sheet(uploaded.name, content)
            if rows:
                if sheet in parsed:
                    current_headers = [_sheet_key(value) for value in parsed[sheet][0]]
                    incoming_headers = [_sheet_key(value) for value in rows[0]]
                    if current_headers != incoming_headers:
                        raise ImportProblem(sheet, 1, "CSV files for the same sheet must have identical column headers.")
                    parsed[sheet].extend(rows[1:])
                else:
                    parsed[sheet] = rows
        else:
            raise ImportProblem("file", 1, "Upload an .xlsx workbook or .csv data files.")
    return parsed


def _table(parsed, sheet, required_headers):
    rows = parsed.get(sheet)
    if not rows:
        return []
    headers = [_sheet_key(value) for value in rows[0]]
    if len(set(headers)) != len(headers):
        raise ImportProblem(sheet, 1, "Column names must be unique.")
    missing = sorted(set(required_headers) - set(headers))
    if missing:
        raise ImportProblem(sheet, 1, f"Missing required columns: {', '.join(missing)}.")
    result = []
    for row_number, values in enumerate(rows[1:], start=2):
        if not any(value not in (None, "") for value in values):
            continue
        result.append(
            {
                "_row": row_number,
                **{
                    header: _cell(values[index] if index < len(values) else None)
                    for index, header in enumerate(headers)
                },
            }
        )
    return result


def _required(row, fields, sheet):
    for field in fields:
        if row.get(field) in (None, ""):
            raise ImportProblem(sheet, row["_row"], f"{field} is required.")


def _decimal(value, sheet, row, field, *, allow_zero=True):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ImportProblem(sheet, row["_row"], f"{field} must be a valid number.") from exc
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        qualifier = "positive" if not allow_zero else "non-negative"
        raise ImportProblem(sheet, row["_row"], f"{field} must be a {qualifier} number.")
    return number


def _date(value, sheet, row, field, *, required=True):
    if value in (None, "") and not required:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ImportProblem(sheet, row["_row"], f"{field} must use YYYY-MM-DD.") from exc


def _integer(value, sheet, row, field, default=None):
    if value in (None, "") and default is not None:
        return default
    if isinstance(value, bool):
        raise ImportProblem(sheet, row["_row"], f"{field} must be a whole number.")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ImportProblem(sheet, row["_row"], f"{field} must be a whole number.") from exc
    if isinstance(value, str) and not re.fullmatch(r"[+-]?\d+", value.strip()):
        raise ImportProblem(sheet, row["_row"], f"{field} must be a whole number.")
    if not isinstance(value, str) and integer != value:
        raise ImportProblem(sheet, row["_row"], f"{field} must be a whole number.")
    return integer


def _datetime(value, sheet, row, field):
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ImportProblem(sheet, row["_row"], f"{field} must be an ISO date-time.") from exc
    return timezone.make_aware(result, timezone.get_current_timezone()) if timezone.is_naive(result) else result


def _normalize_rows(parsed, property_obj=None):
    if property_obj is None and set(parsed).intersection({"units", "tenancies"}):
        raise ImportProblem(
            "workbook",
            1,
            "Units and Tenancies sheets require selecting a property in the migration wizard.",
        )
    if property_obj is not None and parsed.get("properties"):
        raise ImportProblem(
            "properties",
            1,
            "A property-scoped import cannot create or modify properties; remove the Properties sheet.",
        )
    if property_obj is not None and parsed.get("units_tenants"):
        unit_rows = parsed["units_tenants"]
        headers = [_sheet_key(value) for value in unit_rows[0]]
        if "code" in headers and "unit_code" not in headers:
            unit_rows = [list(unit_rows[0])]
            unit_rows[0][headers.index("code")] = "unit_code"
            unit_rows.extend(parsed["units_tenants"][1:])
            parsed["units_tenants"] = unit_rows

    property_header = () if property_obj is not None else ("property_name",)
    required = {
        "properties": ("name", "property_type", "location"),
        "units_tenants": (*property_header, "unit_code", "unit_type", "status"),
        "charges": (*property_header, "unit_code", "charge_type", "name", "amount", "effective_from"),
        "opening_arrears": (
            *property_header, "unit_code", "invoice_number", "period_start",
            "period_end", "due_date", "description", "amount",
        ),
        "payments": (*property_header, "unit_code", "reference", "amount", "source", "received_at"),
        "meter_readings": (
            *property_header, "unit_code", "meter", "period", "previous_value", "current_value",
        ),
        "deposits": (
            *property_header, "unit_code", "entry_type", "amount", "reference", "description",
        ),
    }
    tables = {
        sheet: _table(parsed, sheet, headers)
        for sheet, headers in required.items()
    }
    if not any(tables.values()):
        raise ImportProblem("file", 1, "The upload contains no data rows.")
    return tables


def _rename_headers(rows, aliases, sheet):
    if not rows:
        return
    rows[0] = list(rows[0])
    headers = [_sheet_key(value) for value in rows[0]]
    for index, header in enumerate(headers):
        if header in aliases:
            rows[0][index] = aliases[header]
    normalized = [_sheet_key(value) for value in rows[0]]
    if len(set(normalized)) != len(normalized):
        raise ImportProblem(sheet, 1, "Column names must be unique after applying supported aliases.")


def _normalize_property_inventory(parsed):
    supported = {"units", "tenancies"}
    unsupported = set(parsed) - supported
    if unsupported:
        sheet = sorted(unsupported)[0]
        raise ImportProblem(
            sheet,
            1,
            "Property migration workbooks support only Units and Tenancies sheets. "
            "Opening arrears and water readings belong in the corresponding unit or tenancy row; "
            "payments are not imported.",
        )
    if "units" not in parsed:
        raise ImportProblem("units", 1, "A Units worksheet or units.csv file is required.")

    _rename_headers(
        parsed["units"],
        {
            "code": "unit_code",
            "status": "occupancy_status",
            "water_meter": "water_meter_no",
            "water_meter_number": "water_meter_no",
            "kplc_meter": "kplc_meter_no",
            "kplc_meter_number": "kplc_meter_no",
        },
        "units",
    )
    if "tenancies" in parsed:
        _rename_headers(
            parsed["tenancies"],
            {
                "code": "unit_code",
                "tenant_id": "tenant_id_number",
                "kra_pin": "tenant_kra_pin",
                "arrears": "opening_arrears",
                "arrears_as_of_date": "arrears_as_of",
            },
            "tenancies",
        )

    tables = {
        "units": _table(
            parsed,
            "units",
            ("unit_code", "unit_type", "occupancy_status"),
        ),
        "tenancies": _table(parsed, "tenancies", ("unit_code",)),
    }
    if not tables["units"]:
        raise ImportProblem("units", 1, "The Units worksheet must contain at least one unit row.")
    return tables


def _problem(sheet, row, message):
    raise ImportProblem(sheet, row["_row"], message)


def _prepare_tenant_activation(
    tenant,
    tenancy,
    activation_emails,
    activation_keys,
    sheet,
    row,
):
    activation_key = tenant.pk
    if activation_key in activation_keys:
        return
    if not tenant.email:
        _problem(sheet, row, "Occupied tenant records need tenant_email to receive an activation link and code.")
    if not settings.PUBLIC_SITE_URL:
        _problem(sheet, row, "PUBLIC_SITE_URL must be configured before importing tenants for activation.")

    tenant_role, _ = Group.objects.get_or_create(name=TENANT_PORTAL_ROLE)

    user_model = get_user_model()
    user = tenant.portal_user
    if user is not None:
        has_tenant_role = user.groups.filter(name=TENANT_PORTAL_ROLE).exists()
        if not has_tenant_role:
            has_tenant_role = UserProfile.objects.filter(
                user=user,
                agency_memberships__roles__name=TENANT_PORTAL_ROLE,
            ).exists()
        if not has_tenant_role:
            _problem(sheet, row, "The tenant is linked to a non-tenant portal account; resolve the account conflict first.")
        if user.is_active:
            return
        if user.email and user.email.casefold() != tenant.email.casefold():
            _problem(
                sheet,
                row,
                "The tenant email differs from the email on the linked portal account.",
            )

    if user is None:
        username = tenant.email.strip().lower()
        if user_model.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=username)
        ).exists():
            _problem(
                sheet,
                row,
                "The tenant email already belongs to another account. Resolve the account conflict before importing.",
            )
        user = user_model(username=username, email=username, is_active=False)
        user.set_unusable_password()
        user.full_clean()
        user.save()
        profile = UserProfile(user=user, name=tenant.name, phone=tenant.phone)
        profile.full_clean()
        profile.save()
        profile.branches.add(tenant.branch)
        tenant.portal_user = user
        tenant.save(update_fields=["portal_user", "updated_at"])
    else:
        if not user.email:
            username = tenant.email.strip().lower()
            if user_model.objects.filter(
                Q(username__iexact=username) | Q(email__iexact=username)
            ).exclude(pk=user.pk).exists():
                _problem(sheet, row, "The tenant email already belongs to another account.")
            user.email = username
            user.username = username
            user.save(update_fields=["email", "username"])
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={"name": tenant.name, "phone": tenant.phone},
        )
        profile.branches.add(tenant.branch)

    user.groups.add(tenant_role)
    membership, _ = AgencyMembership.objects.get_or_create(
        profile=profile,
        agency=tenant.branch.agency,
    )
    membership.branches.add(tenant.branch)
    membership.roles.add(tenant_role)

    from users.views import _activation_hash, _new_tenant_activation_code

    token = secrets.token_urlsafe(32)
    activation_code = _new_tenant_activation_code()
    now = timezone.now()
    TenantActivationChallenge.objects.filter(
        tenant=tenant,
        used_at__isnull=True,
    ).update(used_at=now)
    challenge = TenantActivationChallenge.objects.create(
        tenant=tenant,
        unit=tenancy.unit,
        token_hash=_activation_hash(token),
        code_hash=_activation_hash(activation_code),
        expires_at=now + timedelta(hours=48),
    )
    activation_emails.append(
        {
            "challenge_id": challenge.pk,
            "full_name": tenant.name,
            "email": tenant.email,
            "unit_code": tenancy.unit.code,
            "property_name": tenancy.unit.property.name,
            "activation_url": f"{settings.PUBLIC_SITE_URL.rstrip('/')}/activate?token={token}",
            "activation_code": activation_code,
        }
    )
    activation_keys.add(activation_key)


def _month_start(value, sheet, row, field):
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value.strip()):
        year, month = (int(part) for part in value.strip().split("-"))
        parsed = date(year, month, 1)
    else:
        parsed = _date(value, sheet, row, field)
    if parsed.day != 1:
        raise ImportProblem(sheet, row["_row"], f"{field} must be the first day of a month or use YYYY-MM.")
    if parsed > timezone.localdate():
        raise ImportProblem(sheet, row["_row"], f"{field} cannot be in the future.")
    return parsed


def _unit_type_definitions(property_obj):
    definitions = {
        unit_type.name.casefold(): unit_type
        for unit_type in property_obj.unit_types.prefetch_related("utilities").all()
    }
    if definitions:
        return definitions
    legacy_name = property_obj.default_unit_type.strip()
    if not legacy_name:
        return {}
    utilities = []
    if (
        property_obj.billing_mode == Property.BILLING_MODE_METERED_WATER
        and property_obj.default_water_rate > 0
    ):
        utilities.append(
            {
                "utility_type": UtilityConfiguration.UTILITY_WATER,
                "name": "Water",
                "billing_method": UtilityConfiguration.METHOD_METERED,
                "rate_per_unit": property_obj.default_water_rate,
                "fixed_amount": None,
                "deposit_amount": property_obj.water_deposit,
            }
        )
    if property_obj.default_garbage_charge > 0:
        utilities.append(
            {
                "utility_type": UtilityConfiguration.UTILITY_OTHER,
                "name": "Garbage",
                "billing_method": UtilityConfiguration.METHOD_FIXED,
                "fixed_amount": property_obj.default_garbage_charge,
                "rate_per_unit": None,
                "deposit_amount": None,
            }
        )
    return {
        legacy_name.casefold(): {
            "name": legacy_name,
            "monthly_rent": property_obj.default_rent,
            "security_deposit": property_obj.security_deposit,
            "utilities": utilities,
        }
    }


def _unit_type_values(unit_type):
    if isinstance(unit_type, dict):
        return unit_type
    return {
        "name": unit_type.name,
        "monthly_rent": unit_type.monthly_rent,
        "security_deposit": unit_type.security_deposit,
        "utilities": list(unit_type.utilities.all()),
    }


def _utility_values(utility):
    if isinstance(utility, dict):
        return utility
    return {
        "utility_type": utility.utility_type,
        "name": utility.name,
        "billing_method": utility.billing_method,
        "fixed_amount": utility.fixed_amount,
        "rate_per_unit": utility.rate_per_unit,
        "deposit_amount": utility.deposit_amount,
    }


def _import_property_inventory(property_obj, user, tables, *, commit):
    created = {
        "units": 0,
        "tenancies": 0,
        "unit_charges": 0,
        "utilities": 0,
        "opening_arrears": 0,
        "meter_readings": 0,
        "deposit_accounts": 0,
    }
    skipped = []
    activation_emails = []
    activation_keys = set()
    unit_types = _unit_type_definitions(property_obj)
    property_has_water = any(
        utility["utility_type"] == UtilityConfiguration.UTILITY_WATER
        for definition in unit_types.values()
        for utility in (
            _utility_values(item)
            for item in _unit_type_values(definition)["utilities"]
        )
    )
    if not unit_types:
        return {
            "created": created,
            "skipped": skipped,
            "errors": [{
                "sheet": "units",
                "row": 1,
                "message": "Register at least one unit type for this property before importing its inventory.",
            }],
            "activation": {"prepared": 0, "sent": 0, "failed": []},
        }

    def skip(sheet, row, reason):
        skipped.append({"sheet": sheet, "row": row["_row"], "reason": reason})

    def problem(sheet, row, message):
        raise ImportProblem(sheet, row["_row"], message)

    unit_rows = {}
    tenancy_rows = {}
    for row in tables["units"]:
        _required(row, ("unit_code", "unit_type", "occupancy_status"), "units")
        code = str(row["unit_code"]).strip()
        if not code or len(code) > 100:
            problem("units", row, "unit_code must contain 1 to 100 characters.")
        key = code.casefold()
        if key in unit_rows:
            problem("units", row, "Each unit_code may appear only once in the Units worksheet.")
        unit_rows[key] = row
        status_map = {value.casefold(): value for value, _ in Unit.STATUS_CHOICES}
        status = status_map.get(str(row["occupancy_status"]).strip().casefold())
        if status not in {
            Unit.STATUS_VACANT,
            Unit.STATUS_OCCUPIED,
            Unit.STATUS_NOTICE_GIVEN,
        }:
            problem("units", row, "occupancy_status must be Vacant, Occupied, or Notice Given.")
        row["_normalized_status"] = status
        unit_type = unit_types.get(str(row["unit_type"]).strip().casefold())
        if unit_type is None:
            available = ", ".join(sorted(_unit_type_values(item)["name"] for item in unit_types.values()))
            problem(
                "units",
                row,
                f"unit_type must match a saved property definition. Available types: {available}.",
            )
        row["_unit_type_definition"] = unit_type
        if row.get("property_name") not in (None, "") and str(row["property_name"]).strip().casefold() != property_obj.name.casefold():
            problem("units", row, "property_name must match the property selected for this import.")
        water_meter = str(row.get("water_meter_no") or "").strip()
        kplc_meter = str(row.get("kplc_meter_no") or "").strip()
        water_reading = row.get("water_current_reading")
        if property_has_water:
            _required(row, ("water_meter_no", "water_current_reading"), "units")
            if len(water_meter) > 100:
                problem("units", row, "water_meter_no cannot exceed 100 characters.")
            if len(kplc_meter) > 100:
                problem("units", row, "kplc_meter_no cannot exceed 100 characters.")
            row["_water_current_reading"] = _decimal(
                water_reading,
                "units",
                row,
                "water_current_reading",
            )
        elif water_meter or water_reading not in (None, ""):
            problem(
                "units",
                row,
                "Water meter fields can only be supplied when the selected property has a saved water utility.",
            )
        else:
            if len(kplc_meter) > 100:
                problem("units", row, "kplc_meter_no cannot exceed 100 characters.")
            row["_water_current_reading"] = None
        row["_water_meter_no"] = water_meter
        row["_kplc_meter_no"] = kplc_meter

    for row in tables["tenancies"]:
        _required(row, ("unit_code",), "tenancies")
        key = str(row["unit_code"]).strip().casefold()
        if key in tenancy_rows:
            problem("tenancies", row, "Each unit_code may appear only once in the Tenancies worksheet.")
        if key not in unit_rows:
            problem("tenancies", row, "The unit_code must refer to a row in the Units worksheet.")
        unit_status = unit_rows[key]["_normalized_status"]
        if unit_status not in {Unit.STATUS_OCCUPIED, Unit.STATUS_NOTICE_GIVEN}:
            problem("tenancies", row, "Tenancy details can only be supplied for an Occupied or Notice Given unit.")
        expected_move_out_date = _date(
            row.get("expected_move_out_date"),
            "tenancies",
            row,
            "expected_move_out_date",
            required=False,
        )
        if expected_move_out_date and unit_status != Unit.STATUS_NOTICE_GIVEN:
            problem(
                "tenancies",
                row,
                "expected_move_out_date is only valid for a Notice Given unit.",
            )
        row["_expected_move_out_date"] = expected_move_out_date
        if row.get("property_name") not in (None, "") and str(row["property_name"]).strip().casefold() != property_obj.name.casefold():
            problem("tenancies", row, "property_name must match the property selected for this import.")
        tenancy_rows[key] = row

    for key, row in unit_rows.items():
        if (
            row["_normalized_status"] in {Unit.STATUS_OCCUPIED, Unit.STATUS_NOTICE_GIVEN}
            and key not in tenancy_rows
        ):
            problem(
                "units",
                row,
                "Occupied and Notice Given units need a matching row in the Tenancies worksheet.",
            )
    if not tables["units"] and not tables["tenancies"]:
        raise ImportProblem("file", 1, "The upload contains no unit or tenancy data rows.")

    try:
        with transaction.atomic():
            units = {}
            for key, row in unit_rows.items():
                code = str(row["unit_code"]).strip()
                existing = Unit.objects.filter(property=property_obj, code__iexact=code).first()
                if existing:
                    if existing.status != row["_normalized_status"]:
                        problem(
                            "units",
                            row,
                            "This unit already exists with a different occupancy status; existing inventory was not changed.",
                        )
                    expected_type = _unit_type_values(row["_unit_type_definition"])["name"]
                    if existing.unit_type.casefold() != expected_type.casefold():
                        problem(
                            "units",
                            row,
                            "This unit already exists with a different unit type; existing inventory was not changed.",
                        )
                    skip("units", row, "This unit already exists; its inventory and charges were not changed.")
                    units[key] = existing
                    continue

                unit_type = _unit_type_values(row["_unit_type_definition"])
                label = str(row.get("unit_label") or "").strip()
                block_name = str(row.get("block_name") or "").strip()
                floor_name = str(row.get("floor_name") or "").strip()
                floor = None
                if block_name or floor_name or row.get("floor_level") not in (None, ""):
                    if not block_name and floor_name:
                        block_name = "Main"
                    if block_name and not floor_name:
                        problem("units", row, "floor_name is required when block_name is supplied.")
                    floor_level = _integer(row.get("floor_level"), "units", row, "floor_level", 0)
                    block, _ = Block.objects.get_or_create(
                        property=property_obj,
                        name=block_name,
                    )
                    floor, _ = Floor.objects.get_or_create(
                        block=block,
                        level=floor_level,
                        defaults={"name": floor_name},
                    )
                    if floor_name and floor.name.casefold() != floor_name.casefold():
                        problem("units", row, "The selected block already has a different floor name at this floor level.")
                bedrooms = _integer(row.get("bedrooms"), "units", row, "bedrooms", 0)
                if bedrooms < 0:
                    problem("units", row, "bedrooms cannot be negative.")
                characteristics = {}
                if label:
                    characteristics["room_name"] = label
                unit = Unit(
                    property=property_obj,
                    unit_type_definition=(
                        row["_unit_type_definition"]
                        if isinstance(row["_unit_type_definition"], PropertyUnitType)
                        else None
                    ),
                    floor=floor,
                    code=code,
                    unit_type=unit_type["name"],
                    bedrooms=bedrooms,
                    characteristics=characteristics,
                    meters={},
                    status=row["_normalized_status"],
                    is_migrated=True,
                )
                unit.full_clean()
                unit.save()
                units[key] = unit
                created["units"] += 1

                tenancy_row = tenancy_rows.get(key)
                tenancy_start = (
                    _date(
                        tenancy_row.get("tenancy_start_date"),
                        "tenancies",
                        tenancy_row,
                        "tenancy_start_date",
                    )
                    if tenancy_row
                    else None
                )
                effective_from = tenancy_start or timezone.localdate()
                rent_charge = UnitCharge(
                    unit=unit,
                    charge_type=UnitCharge.CHARGE_RENT,
                    charge_code="rent",
                    name="Rent",
                    amount=_decimal(
                        unit_type["monthly_rent"],
                        "units",
                        row,
                        "monthly_rent",
                    ),
                    effective_from=effective_from,
                )
                rent_charge.full_clean()
                rent_charge.save()
                created["unit_charges"] += 1

                water_meter = row["_water_meter_no"]
                type_utilities = [_utility_values(item) for item in unit_type["utilities"]]
                metered_water = [
                    item
                    for item in type_utilities
                    if item["utility_type"] == UtilityConfiguration.UTILITY_WATER
                    and item["billing_method"] == UtilityConfiguration.METHOD_METERED
                ]
                if len(metered_water) > 1:
                    problem("units", row, "A unit type can have only one metered water utility.")
                if metered_water:
                    if not water_meter:
                        problem("units", row, "water_meter_no is required for a unit type with metered water.")
                if water_meter:
                    unit.meters = {**unit.meters, "water": water_meter}
                if row["_water_current_reading"] is not None:
                    unit.meters = {
                        **unit.meters,
                        "water_reading": str(row["_water_current_reading"]),
                    }
                if row["_kplc_meter_no"]:
                    unit.meters = {**unit.meters, "electricity": row["_kplc_meter_no"]}
                if unit.meters:
                    unit.save(update_fields=["meters", "updated_at"])

                for utility_index, utility in enumerate(type_utilities, start=1):
                    utility_name = str(utility["name"]).strip()
                    if utility["billing_method"] == UtilityConfiguration.METHOD_FIXED:
                        amount = _decimal(
                            utility["fixed_amount"],
                            "units",
                            row,
                            f"{utility_name} fixed amount",
                        )
                        charge = UnitCharge(
                            unit=unit,
                            charge_type=UnitCharge.CHARGE_RECURRING,
                            charge_code=f"utility-{utility_index}",
                            name=utility_name,
                            amount=amount,
                            effective_from=effective_from,
                        )
                        charge.full_clean()
                        charge.save()
                        created["unit_charges"] += 1
                    elif utility["billing_method"] == UtilityConfiguration.METHOD_METERED:
                        if utility["utility_type"] != UtilityConfiguration.UTILITY_WATER:
                            problem(
                                "units",
                                row,
                                f"Metered {utility_name} is not supported by monthly invoicing yet; configure it as fixed or use metered water.",
                            )
                        utility_config = UtilityConfiguration(
                            unit=unit,
                            utility_type=utility["utility_type"],
                            name=utility_name,
                            billing_method=UtilityConfiguration.METHOD_METERED,
                            rate_per_unit=_decimal(
                                utility["rate_per_unit"],
                                "units",
                                row,
                                f"{utility_name} rate",
                            ),
                            effective_from=effective_from,
                            metadata={"meter": water_meter},
                        )
                        utility_config.full_clean()
                        utility_config.save()
                        created["utilities"] += 1
            for key, row in tenancy_rows.items():
                unit = units[key]
                active_tenancy = Tenancy.objects.filter(
                    unit=unit,
                    status=Tenancy.STATUS_ACTIVE,
                ).select_related("tenant").first()
                tenant_email = str(row.get("tenant_email") or "").strip().lower()
                if active_tenancy:
                    if (
                        tenant_email
                        and active_tenancy.tenant.email
                        and tenant_email.casefold() != active_tenancy.tenant.email.casefold()
                    ):
                        problem("tenancies", row, "tenant_email differs from the email on the active tenant record.")
                    if tenant_email and not active_tenancy.tenant.email:
                        active_tenancy.tenant.email = tenant_email
                        active_tenancy.tenant.full_clean()
                        active_tenancy.tenant.save(update_fields=["email", "updated_at"])
                    skip("tenancies", row, "This unit already has an active tenancy; existing tenancy and balances were not changed.")
                    _prepare_tenant_activation(
                        active_tenancy.tenant,
                        active_tenancy,
                        activation_emails,
                        activation_keys,
                        "tenancies",
                        row,
                    )
                    continue

                _required(
                    row,
                    ("tenant_name", "tenant_phone", "tenant_email", "tenancy_start_date"),
                    "tenancies",
                )
                tenant_name = str(row["tenant_name"]).strip()
                phone = str(row["tenant_phone"]).strip()
                if not tenant_name:
                    problem("tenancies", row, "tenant_name cannot be empty.")
                if not tenant_email:
                    problem("tenancies", row, "Occupied tenants need tenant_email for account activation.")
                start_date = _date(row["tenancy_start_date"], "tenancies", row, "tenancy_start_date")
                end_date = _date(
                    row.get("tenancy_end_date"),
                    "tenancies",
                    row,
                    "tenancy_end_date",
                    required=False,
                )
                expected_move_out_date = row["_expected_move_out_date"]
                if (
                    expected_move_out_date
                    and end_date
                    and expected_move_out_date != end_date
                ):
                    problem(
                        "tenancies",
                        row,
                        "tenancy_end_date must match expected_move_out_date when both are supplied.",
                    )
                end_date = expected_move_out_date or end_date
                tenant_type = str(
                    row.get("tenant_type") or Tenant.INDIVIDUAL
                ).strip().casefold()
                if tenant_type not in dict(Tenant.TENANT_TYPE_CHOICES):
                    problem("tenancies", row, "tenant_type must be individual or company.")
                identity = str(row.get("tenant_id_number") or "").strip()
                registration = str(row.get("company_registration_number") or "").strip()
                contact_person = str(row.get("contact_person") or "").strip()
                tenant = Tenant.objects.filter(branch=property_obj.branch, phone=phone).first()
                if tenant is None:
                    tenant = Tenant(
                        branch=property_obj.branch,
                        tenant_type=tenant_type,
                        name=tenant_name,
                        phone=phone,
                        email=tenant_email,
                        national_id_or_passport=identity,
                        kra_pin=str(row.get("tenant_kra_pin") or "").strip(),
                        is_provisional=not identity and tenant_type == Tenant.INDIVIDUAL,
                        company_registration_number=registration,
                        contact_person=contact_person,
                    )
                    tenant.full_clean()
                    tenant.save()
                else:
                    if tenant.email and tenant.email.casefold() != tenant_email.casefold():
                        problem("tenancies", row, "tenant_email differs from the email on the existing tenant record.")
                    if not tenant.email:
                        tenant.email = tenant_email
                        tenant.full_clean()
                        tenant.save(update_fields=["email", "updated_at"])
                    skip("tenancies", row, "An existing tenant matched this phone number; their contact details were reused.")

                tenancy_type = str(
                    row.get("tenancy_type") or Tenancy.TYPE_MONTH_TO_MONTH
                ).strip().casefold()
                unit_reading = unit_rows[key]["_water_current_reading"]
                legacy_base_reading = row.get("base_water_reading")
                if (
                    unit_reading is not None
                    and legacy_base_reading not in (None, "")
                    and _decimal(
                        legacy_base_reading,
                        "tenancies",
                        row,
                        "base_water_reading",
                    ) != unit_reading
                ):
                    problem(
                        "tenancies",
                        row,
                        "base_water_reading must match water_current_reading from the Units sheet.",
                    )
                base_reading = (
                    unit_reading
                    if unit_reading is not None
                    else _decimal(
                        legacy_base_reading,
                        "tenancies",
                        row,
                        "base_water_reading",
                    )
                    if legacy_base_reading not in (None, "")
                    else None
                )
                tenancy = Tenancy(
                    unit=unit,
                    tenant=tenant,
                    tenancy_type=tenancy_type,
                    start_date=start_date,
                    end_date=end_date,
                    base_water_meter_reading=base_reading,
                    due_day=_integer(
                        row.get("due_day"),
                        "tenancies",
                        row,
                        "due_day",
                        property_obj.rent_due_day,
                    ),
                    status=Tenancy.STATUS_ACTIVE,
                )
                tenancy.full_clean()
                tenancy.save()
                created["tenancies"] += 1
                target_deposit = sum(
                    (
                        _decimal(
                            _unit_type_values(unit_types[unit.unit_type.casefold()])["security_deposit"],
                            "tenancies",
                            row,
                            "security_deposit",
                        ),
                        *(
                            _decimal(
                                utility["deposit_amount"],
                                "tenancies",
                                row,
                                f"{utility['name']} deposit",
                            )
                            for utility in (
                                _utility_values(item)
                                for item in _unit_type_values(
                                    unit_types[unit.unit_type.casefold()]
                                )["utilities"]
                            )
                            if utility["deposit_amount"] not in (None, "")
                        ),
                    ),
                    Decimal("0.00"),
                )
                _, account_created = DepositAccount.objects.get_or_create(
                    tenancy=tenancy,
                    defaults={"target_amount": target_deposit},
                )
                if account_created:
                    created["deposit_accounts"] += 1

                arrears_value = row.get("opening_arrears")
                arrears_date_value = row.get("arrears_as_of")
                if arrears_value not in (None, ""):
                    arrears_amount = _decimal(
                        arrears_value,
                        "tenancies",
                        row,
                        "opening_arrears",
                    )
                    if arrears_amount > 0:
                        if arrears_date_value in (None, ""):
                            problem("tenancies", row, "arrears_as_of is required when opening_arrears is greater than zero.")
                        arrears_as_of = _date(
                            arrears_date_value,
                            "tenancies",
                            row,
                            "arrears_as_of",
                        )
                        if arrears_as_of > timezone.localdate():
                            problem("tenancies", row, "arrears_as_of cannot be in the future.")
                        invoice_number = (
                            f"MIG-{property_obj.branch_id}-{tenancy.pk}-"
                            f"{arrears_as_of:%Y%m%d}"
                        )
                        if Invoice.objects.filter(
                            tenancy=tenancy,
                            period_start=arrears_as_of,
                            period_end=arrears_as_of,
                        ).exists():
                            problem("tenancies", row, "An invoice already exists for the opening-arrears date.")
                        invoice = Invoice(
                            branch=property_obj.branch,
                            tenancy=tenancy,
                            number=invoice_number,
                            period_start=arrears_as_of,
                            period_end=arrears_as_of,
                            due_date=arrears_as_of,
                            total=arrears_amount,
                        )
                        invoice.full_clean()
                        invoice.save()
                        InvoiceLine.objects.create(
                            invoice=invoice,
                            charge_type="arrears",
                            description="Opening arrears at migration",
                            amount=arrears_amount,
                            metadata={
                                "migration_opening_balance": True,
                                "as_of": arrears_as_of.isoformat(),
                            },
                        )
                        created["opening_arrears"] += 1
                elif arrears_date_value not in (None, ""):
                    problem("tenancies", row, "opening_arrears is required when arrears_as_of is supplied.")

                _prepare_tenant_activation(
                    tenant,
                    tenancy,
                    activation_emails,
                    activation_keys,
                    "tenancies",
                    row,
                )

            report = {
                "created": created,
                "skipped": skipped,
                "errors": [],
                "activation": {
                    "prepared": len(activation_emails),
                    "sent": 0,
                    "failed": [],
                },
            }
            if not commit:
                raise PreviewComplete(report)
    except PreviewComplete as preview:
        return preview.report
    except ImportProblem as exc:
        return {
            "created": {key: 0 for key in created},
            "skipped": skipped,
            "errors": [exc.issue],
            "activation": {"prepared": 0, "sent": 0, "failed": []},
        }
    except ValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else str(exc)
        return {
            "created": {key: 0 for key in created},
            "skipped": skipped,
            "errors": [{"sheet": "batch", "row": 1, "message": str(detail)}],
            "activation": {"prepared": 0, "sent": 0, "failed": []},
        }
    except IntegrityError:
        return {
            "created": {key: 0 for key in created},
            "skipped": skipped,
            "errors": [{
                "sheet": "batch",
                "row": 1,
                "message": "A record conflicted with existing inventory or ledger data. No records were imported; preview the file again.",
            }],
            "activation": {"prepared": 0, "sent": 0, "failed": []},
        }

    _deliver_activation_emails(report, activation_emails)
    return report


def _deliver_activation_emails(report, activation_emails):
    if not activation_emails:
        return
    from users.views import _send_tenant_activation_email

    for invitation in activation_emails:
        try:
            _send_tenant_activation_email(
                invitation["full_name"],
                invitation["email"],
                invitation["unit_code"],
                invitation["property_name"],
                invitation["activation_url"],
                invitation["activation_code"],
            )
        except (
            ImproperlyConfigured,
            OSError,
            RuntimeError,
            smtplib.SMTPException,
            ValueError,
        ) as error:
            TenantActivationChallenge.objects.filter(
                pk=invitation["challenge_id"]
            ).update(email_delivery_error=type(error).__name__)
            logger.warning(
                "Legacy tenant activation email delivery failed (%s).",
                type(error).__name__,
            )
            report["activation"]["failed"].append(
                {
                    "unit_code": invitation["unit_code"],
                    "email": invitation["email"],
                    "message": "Activation email delivery failed. Re-import the property unit data to issue a fresh link and code.",
                }
            )
        else:
            TenantActivationChallenge.objects.filter(
                pk=invitation["challenge_id"]
            ).update(email_sent_at=timezone.now(), email_delivery_error="")
            report["activation"]["sent"] += 1


def import_legacy_data(branch, user, files, *, commit, property_obj=None):
    try:
        parsed = _parse_files(files)
        if property_obj is not None:
            tables = _normalize_property_inventory(parsed)
            return _import_property_inventory(
                property_obj,
                user,
                tables,
                commit=commit,
            )
        tables = _normalize_rows(parsed, property_obj=property_obj)
    except ImportProblem as exc:
        return {"errors": [exc.issue], "skipped": [], "created": {}}

    created = {sheet: 0 for sheet in SHEET_NAMES}
    skipped = []
    properties = {
        prop.name.casefold(): prop
        for prop in Property.objects.filter(branch=branch)
    }
    if property_obj is not None:
        properties = {property_obj.name.casefold(): property_obj}
    units = {}
    tenancies = {}
    invoices = {}
    invoice_allocated = {}
    issues = []
    activation_emails = []
    activation_keys = set()

    def skip(sheet, row, reason):
        skipped.append({"sheet": sheet, "row": row["_row"], "reason": reason})

    def fail(sheet, row, message):
        issues.append({"sheet": sheet, "row": row["_row"], "message": message})

    def row_property(sheet, row):
        if property_obj is not None:
            requested_name = str(row.get("property_name") or "").strip()
            if requested_name and requested_name.casefold() != property_obj.name.casefold():
                _problem(sheet, row, "property_name must match the property selected for this import.")
            return property_obj
        property_name = str(row.get("property_name") or "").strip()
        prop = properties.get(property_name.casefold())
        if prop is None:
            _problem(sheet, row, "property_name must match a property in this branch or the Properties sheet.")
        return prop

    try:
        with transaction.atomic():
            for row in tables["properties"]:
                _required(row, ("name", "property_type", "location"), "properties")
                key = str(row["name"]).casefold()
                if key in properties:
                    skip("properties", row, "A property with this name already exists in the selected branch; it was reused without changes.")
                    continue
                due_day = _integer(row.get("rent_due_day"), "properties", row, "rent_due_day", 10)
                prop = Property(
                    branch=branch,
                    name=str(row["name"]).strip(),
                    property_type=str(row["property_type"]).strip(),
                    location=str(row["location"]).strip(),
                    address=str(row.get("address") or "").strip(),
                    rent_due_day=due_day,
                )
                prop.full_clean()
                prop.save()
                properties[key] = prop
                created["properties"] += 1

            for row in tables["units_tenants"]:
                sheet = "units_tenants"
                _required(row, ("unit_code", "unit_type", "status"), sheet)
                prop = row_property(sheet, row)
                key = (prop.pk, str(row["unit_code"]).strip().casefold())
                unit = units.get(key) or Unit.objects.filter(
                    property=prop,
                    code__iexact=str(row["unit_code"]).strip(),
                ).first()
                if unit:
                    skip(sheet, row, "This unit already exists; the existing unit and tenant data were not changed.")
                    units[key] = unit
                    active_tenancy = Tenancy.objects.filter(
                        unit=unit,
                        status=Tenancy.STATUS_ACTIVE,
                    ).select_related("tenant").first()
                    if active_tenancy:
                        tenancies[key] = active_tenancy
                        if (
                            property_obj is not None
                            and str(row.get("status", "")).strip().casefold()
                            == Unit.STATUS_OCCUPIED.casefold()
                        ):
                            imported_email = str(row.get("tenant_email") or "").strip().lower()
                            if imported_email and not active_tenancy.tenant.email:
                                active_tenancy.tenant.email = imported_email
                                active_tenancy.tenant.full_clean()
                                active_tenancy.tenant.save(update_fields=["email", "updated_at"])
                            elif (
                                imported_email
                                and active_tenancy.tenant.email
                                and imported_email.casefold()
                                != active_tenancy.tenant.email.casefold()
                            ):
                                _problem(
                                    sheet,
                                    row,
                                    "tenant_email differs from the email on the existing tenant record.",
                                )
                            _prepare_tenant_activation(
                                active_tenancy.tenant,
                                active_tenancy,
                                activation_emails,
                                activation_keys,
                                sheet,
                                row,
                            )
                    continue
                status_map = {value.casefold(): value for value, _ in Unit.STATUS_CHOICES}
                status = status_map.get(str(row["status"]).strip().casefold())
                if status not in {Unit.STATUS_VACANT, Unit.STATUS_OCCUPIED}:
                    _problem(sheet, row, "New units may only be imported as Vacant or Occupied.")
                bedrooms = _integer(row.get("bedrooms"), sheet, row, "bedrooms", 0)
                unit = Unit(
                    property=prop,
                    code=str(row["unit_code"]).strip(),
                    unit_type=str(row["unit_type"]).strip(),
                    bedrooms=bedrooms,
                    status=status,
                    is_migrated=True,
                )
                unit.full_clean()
                unit.save()
                units[key] = unit
                created["units_tenants"] += 1

                tenant_name = str(row.get("tenant_name") or "").strip()
                phone = str(row.get("tenant_phone") or "").strip()
                start_date = _date(row.get("tenancy_start_date"), sheet, row, "tenancy_start_date", required=False)
                if status == Unit.STATUS_OCCUPIED:
                    if not tenant_name or not phone or start_date is None:
                        _problem(sheet, row, "Occupied units require tenant_name, tenant_phone, and tenancy_start_date.")
                    tenant_email = str(row.get("tenant_email") or "").strip().lower()
                    if property_obj is not None and not tenant_email:
                        _problem(sheet, row, "Occupied tenant records need tenant_email to receive an activation link and code.")
                    tenant = Tenant.objects.filter(branch=branch, phone=phone).first()
                    if tenant is None:
                        tenant_type = str(row.get("tenant_type") or Tenant.INDIVIDUAL).strip().casefold()
                        if tenant_type not in dict(Tenant.TENANT_TYPE_CHOICES):
                            _problem(sheet, row, "tenant_type must be individual or company.")
                        identity = str(row.get("tenant_id_number") or "").strip()
                        tenant = Tenant(
                            branch=branch,
                            tenant_type=tenant_type,
                            name=tenant_name,
                            phone=phone,
                            email=tenant_email,
                            national_id_or_passport=identity,
                            is_provisional=not identity and tenant_type == Tenant.INDIVIDUAL,
                            company_registration_number=str(row.get("company_registration_number") or "").strip(),
                            contact_person=str(row.get("contact_person") or "").strip(),
                        )
                        tenant.full_clean()
                        tenant.save()
                    else:
                        skip(sheet, row, "An existing tenant matched this phone number; their contact record was reused without changes.")
                        if property_obj is not None:
                            if tenant.email and tenant_email and tenant.email.casefold() != tenant_email.casefold():
                                _problem(sheet, row, "tenant_email differs from the email on the existing tenant record.")
                            if not tenant.email and tenant_email:
                                tenant.email = tenant_email
                                tenant.full_clean()
                                tenant.save(update_fields=["email", "updated_at"])
                    end_date = _date(row.get("tenancy_end_date"), sheet, row, "tenancy_end_date", required=False)
                    tenancy_type = str(row.get("tenancy_type") or Tenancy.TYPE_MONTH_TO_MONTH).strip()
                    tenancy = Tenancy(
                        unit=unit,
                        tenant=tenant,
                        tenancy_type=tenancy_type,
                        start_date=start_date,
                        end_date=end_date,
                        due_day=_integer(row.get("due_day"), sheet, row, "due_day", 1),
                        status=Tenancy.STATUS_ACTIVE,
                    )
                    tenancy.full_clean()
                    tenancy.save()
                    tenancies[key] = tenancy
                    if property_obj is not None:
                        _prepare_tenant_activation(
                            tenant,
                            tenancy,
                            activation_emails,
                            activation_keys,
                            sheet,
                            row,
                        )
                if row.get("rent") not in (None, ""):
                    rent = _decimal(row["rent"], sheet, row, "rent")
                    if rent > 0:
                        rent_default_date = start_date or timezone.localdate()
                        effective_from = _date(
                            row.get("effective_from") or rent_default_date,
                            sheet,
                            row,
                            "effective_from",
                        )
                        rent_charge = UnitCharge(
                            unit=unit,
                            charge_type=UnitCharge.CHARGE_RENT,
                            charge_code="rent",
                            name="Rent",
                            amount=rent,
                            effective_from=effective_from,
                        )
                        rent_charge.full_clean()
                        rent_charge.save()
                        created["charges"] += 1

            for row in tables["charges"]:
                sheet = "charges"
                _required(row, ("unit_code", "charge_type", "name", "amount", "effective_from"), sheet)
                prop = row_property(sheet, row)
                unit = Unit.objects.filter(
                    property=prop,
                    code__iexact=str(row["unit_code"]).strip(),
                ).first() if prop else None
                if unit is None:
                    _problem(sheet, row, "The property and unit must exist in the selected branch or Units_Tenants sheet.")
                effective_from = _date(row["effective_from"], sheet, row, "effective_from")
                effective_to = _date(row.get("effective_to"), sheet, row, "effective_to", required=False)
                amount = _decimal(row["amount"], sheet, row, "amount")
                charge_type = str(row["charge_type"]).strip()
                charge_code = str(row.get("charge_code") or "default").strip()
                existing = UnitCharge.objects.filter(
                    unit=unit,
                    charge_type=charge_type,
                    charge_code=charge_code,
                    effective_from__lte=effective_to or date.max,
                ).filter(
                    models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=effective_from)
                ).exists()
                if existing:
                    skip(sheet, row, "This charge period overlaps an existing charge; it was not changed.")
                    continue
                charge = UnitCharge(
                    unit=unit,
                    charge_type=charge_type,
                    charge_code=charge_code,
                    name=str(row["name"]).strip(),
                    amount=amount,
                    effective_from=effective_from,
                    effective_to=effective_to,
                )
                charge.full_clean()
                charge.save()
                created["charges"] += 1

            for row in tables["opening_arrears"]:
                sheet = "opening_arrears"
                _required(row, ("unit_code", "invoice_number", "period_start", "period_end", "due_date", "description", "amount"), sheet)
                prop = row_property(sheet, row)
                unit = Unit.objects.filter(
                    property=prop,
                    code__iexact=str(row["unit_code"]).strip(),
                ).first() if prop else None
                tenancy = Tenancy.objects.filter(
                    unit=unit,
                    status=Tenancy.STATUS_ACTIVE,
                ).first() if unit else None
                if tenancy is None:
                    _problem(sheet, row, "An active tenancy is required to record opening arrears.")
                invoice_number = str(row["invoice_number"]).strip()
                if Invoice.objects.filter(number=invoice_number).exists() or invoice_number in invoices:
                    skip(sheet, row, "This invoice number already exists; the existing ledger was not changed.")
                    continue
                amount = _decimal(row["amount"], sheet, row, "amount")
                period_start = _date(row["period_start"], sheet, row, "period_start")
                period_end = _date(row["period_end"], sheet, row, "period_end")
                due_date = _date(row["due_date"], sheet, row, "due_date")
                if Invoice.objects.filter(
                    tenancy=tenancy,
                    period_start=period_start,
                    period_end=period_end,
                ).exists():
                    skip(sheet, row, "An invoice already exists for this tenancy and period.")
                    continue
                invoice = Invoice(
                    branch=branch,
                    tenancy=tenancy,
                    number=invoice_number,
                    period_start=period_start,
                    period_end=period_end,
                    due_date=due_date,
                    total=amount,
                )
                invoice.full_clean()
                invoice.save()
                InvoiceLine.objects.create(
                    invoice=invoice,
                    charge_type="arrears",
                    description=str(row["description"]).strip(),
                    amount=amount,
                    metadata={"migration_opening_balance": True},
                )
                invoices[invoice_number] = invoice
                created["opening_arrears"] += 1

            for row in tables["payments"]:
                sheet = "payments"
                _required(row, ("unit_code", "reference", "amount", "source", "received_at"), sheet)
                prop = row_property(sheet, row)
                unit = Unit.objects.filter(
                    property=prop,
                    code__iexact=str(row["unit_code"]).strip(),
                ).first() if prop else None
                tenancy = Tenancy.objects.filter(
                    unit=unit,
                ).select_related("tenant").order_by("-start_date", "-id").first() if unit else None
                if tenancy is None:
                    _problem(sheet, row, "The property and unit must have a tenancy for this payment.")
                reference = str(row["reference"]).strip()
                if Payment.objects.filter(reference=reference).exists():
                    skip(sheet, row, "This payment reference already exists; the existing ledger was not changed.")
                    continue
                amount = _decimal(row["amount"], sheet, row, "amount", allow_zero=False)
                source = str(row["source"]).strip().casefold()
                if source not in dict(Payment.SOURCE_CHOICES):
                    _problem(sheet, row, "source must be manual, mpesa, bank, cheque, or cash.")
                instrument = str(row.get("instrument_status") or "").strip().casefold()
                if not instrument:
                    instrument = (
                        Payment.INSTRUMENT_PENDING
                        if source == Payment.SOURCE_CHEQUE
                        else Payment.INSTRUMENT_NOT_APPLICABLE
                    )
                if instrument not in dict(Payment.INSTRUMENT_STATUS_CHOICES):
                    _problem(sheet, row, "instrument_status is not supported.")
                payment = Payment(
                    tenant=tenancy.tenant,
                    amount=amount,
                    reference=reference,
                    source=source,
                    instrument_status=instrument,
                    received_at=_datetime(row["received_at"], sheet, row, "received_at"),
                    status=Payment.STATUS_UNMATCHED,
                    notes="Imported historical payment.",
                )
                invoice_number = str(row.get("invoice_number") or "").strip()
                allocation_amount = Decimal("0.00")
                invoice = None
                if invoice_number:
                    invoice = invoices.get(invoice_number) or Invoice.objects.filter(
                        branch=branch,
                        tenancy=tenancy,
                        number=invoice_number,
                    ).first()
                    if invoice is None:
                        _problem(sheet, row, "invoice_number must identify an invoice for this tenancy.")
                    allocation_amount = _decimal(
                        row.get("allocation_amount") or amount,
                        sheet,
                        row,
                        "allocation_amount",
                        allow_zero=False,
                    )
                    if allocation_amount > amount:
                        _problem(sheet, row, "allocation_amount cannot exceed the payment amount.")
                    allocated = invoice_allocated.get(invoice.pk)
                    if allocated is None:
                        allocated = PaymentAllocation.objects.filter(invoice=invoice).aggregate(
                            total=models.Sum("amount")
                        )["total"] or Decimal("0.00")
                    if allocated + allocation_amount > invoice.total:
                        _problem(sheet, row, "The allocation would exceed the invoice total.")
                    invoice_allocated[invoice.pk] = allocated + allocation_amount
                    payment.allocated_amount = allocation_amount
                    payment.status = (
                        Payment.STATUS_ALLOCATED
                        if allocation_amount == amount
                        else Payment.STATUS_PARTIALLY_ALLOCATED
                    )
                elif row.get("allocation_amount") not in (None, ""):
                    _problem(sheet, row, "allocation_amount requires invoice_number.")
                payment.save()
                if invoice and allocation_amount:
                    PaymentAllocation.objects.create(
                        payment=payment,
                        invoice=invoice,
                        amount=allocation_amount,
                    )
                created["payments"] += 1

            for row in tables["meter_readings"]:
                sheet = "meter_readings"
                _required(row, ("unit_code", "meter", "period", "previous_value", "current_value"), sheet)
                prop = row_property(sheet, row)
                unit = Unit.objects.filter(
                    property=prop,
                    code__iexact=str(row["unit_code"]).strip(),
                ).first() if prop else None
                if unit is None:
                    _problem(sheet, row, "The property and unit must exist in the selected branch or Units_Tenants sheet.")
                period = _date(row["period"], sheet, row, "period")
                meter = str(row["meter"]).strip()
                if MeterReading.objects.filter(unit=unit, meter=meter, period=period).exists():
                    skip(sheet, row, "A reading already exists for this unit, meter, and period.")
                    continue
                reading = MeterReading(
                    unit=unit,
                    meter=meter,
                    period=period,
                    previous_value=_decimal(row["previous_value"], sheet, row, "previous_value"),
                    current_value=_decimal(row["current_value"], sheet, row, "current_value"),
                    submitted_by=user,
                )
                reading.full_clean()
                reading.save()
                created["meter_readings"] += 1

            for row in tables["deposits"]:
                sheet = "deposits"
                _required(row, ("unit_code", "entry_type", "amount", "reference", "description"), sheet)
                prop = row_property(sheet, row)
                unit = Unit.objects.filter(
                    property=prop,
                    code__iexact=str(row["unit_code"]).strip(),
                ).first() if prop else None
                tenancy = Tenancy.objects.filter(
                    unit=unit,
                    status=Tenancy.STATUS_ACTIVE,
                ).first() if unit else None
                if tenancy is None:
                    _problem(sheet, row, "An active tenancy is required to record a deposit.")
                entry_type = str(row["entry_type"]).strip().casefold()
                if entry_type not in dict(DepositEntry.TYPE_CHOICES):
                    _problem(sheet, row, "entry_type is not supported.")
                reference = str(row["reference"]).strip()
                if DepositEntry.objects.filter(reference=reference).exists():
                    skip(sheet, row, "This deposit reference already exists; the existing ledger was not changed.")
                    continue
                amount = _decimal(row["amount"], sheet, row, "amount", allow_zero=False)
                account, _ = DepositAccount.objects.select_for_update().get_or_create(
                    tenancy=tenancy,
                    defaults={
                        "target_amount": _decimal(
                            row.get("target_amount") or "0",
                            sheet,
                            row,
                            "target_amount",
                        )
                    },
                )
                target_amount = _decimal(
                    row.get("target_amount") or account.target_amount,
                    sheet,
                    row,
                    "target_amount",
                )
                if entry_type in {DepositEntry.TYPE_REFUND, DepositEntry.TYPE_DEDUCTION}:
                    if amount > account.balance:
                        _problem(sheet, row, "A refund or deduction cannot exceed the current deposit balance.")
                    account.balance -= amount
                else:
                    account.balance += amount
                account.target_amount = target_amount
                DepositEntry.objects.create(
                    account=account,
                    entry_type=entry_type,
                    amount=amount,
                    reference=reference,
                    description=str(row["description"]).strip(),
                    created_by=user,
                )
                account.save(update_fields=["balance", "target_amount", "updated_at"])
                created["deposits"] += 1

            if issues:
                raise ImportProblem("batch", 1, "The batch contains invalid rows.")
            report = {
                "created": created,
                "skipped": skipped,
                "errors": [],
                "activation": {
                    "prepared": len(activation_emails),
                    "sent": 0,
                    "failed": [],
                },
            }
            if not commit:
                raise PreviewComplete(report)
    except PreviewComplete as preview:
        return preview.report
    except ImportProblem as exc:
        return {"created": {sheet: 0 for sheet in SHEET_NAMES}, "skipped": skipped, "errors": [*issues, exc.issue]}
    except ValidationError as exc:
        return {
            "created": {sheet: 0 for sheet in SHEET_NAMES},
            "skipped": skipped,
            "errors": [{"sheet": "batch", "row": 1, "message": str(exc.message_dict if hasattr(exc, "message_dict") else exc)}],
        }
    except IntegrityError:
        return {
            "created": {sheet: 0 for sheet in SHEET_NAMES},
            "skipped": skipped,
            "errors": [{"sheet": "batch", "row": 1, "message": "A record conflicted with an existing ledger entry. No records were imported; preview the file again."}],
        }
    if activation_emails:
        from users.views import _send_tenant_activation_email

        for invitation in activation_emails:
            try:
                _send_tenant_activation_email(
                    invitation["full_name"],
                    invitation["email"],
                    invitation["unit_code"],
                    invitation["property_name"],
                    invitation["activation_url"],
                    invitation["activation_code"],
                )
            except (
                ImproperlyConfigured,
                OSError,
                RuntimeError,
                smtplib.SMTPException,
                ValueError,
            ) as error:
                TenantActivationChallenge.objects.filter(
                    pk=invitation["challenge_id"]
                ).update(email_delivery_error=type(error).__name__)
                logger.warning(
                    "Legacy tenant activation email delivery failed (%s).",
                    type(error).__name__,
                )
                report["activation"]["failed"].append(
                    {
                        "unit_code": invitation["unit_code"],
                        "email": invitation["email"],
                        "message": "Activation email delivery failed. Re-import the property unit data to issue a fresh link and code.",
                    }
                )
            else:
                TenantActivationChallenge.objects.filter(
                    pk=invitation["challenge_id"]
                ).update(email_sent_at=timezone.now(), email_delivery_error="")
                report["activation"]["sent"] += 1
    return report
