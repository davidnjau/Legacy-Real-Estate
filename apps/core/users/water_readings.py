import calendar
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from botocore.exceptions import BotoCoreError, ClientError
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError

from users.audit import record_audit_event
from users.billing import month_end
from users.models import (
    MeterReading,
    MeterReadingDraft,
    Property,
    Tenancy,
    UtilityConfiguration,
)
from users.object_storage import delete_private_object, upload_private_object

logger = logging.getLogger(__name__)
MAX_METER_PHOTO_BYTES = 5 * 1024 * 1024


class WaterReadingError(Exception):
    def __init__(self, title, detail, code, status=400):
        super().__init__(detail)
        self.title = title
        self.detail = detail
        self.code = code
        self.status = status


def _delete_evidence(key):
    try:
        delete_private_object(key)
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        logger.exception("Could not remove incomplete water meter evidence %s.", key)


def caretaker_water_period():
    today = timezone.localdate()
    return (today.replace(day=28) + timedelta(days=4)).replace(day=1)


def caretaker_water_readings_open_date(today=None):
    today = today or timezone.localdate()
    if today.month == 2:
        due_day = 28 if calendar.isleap(today.year) else 27
    else:
        due_day = 30
    return today.replace(day=due_day)


def caretaker_water_readings_due():
    today = timezone.localdate()
    return today >= caretaker_water_readings_open_date(today)


def caretaker_water_units(property_obj, period_start):
    tenancies = (
        Tenancy.objects.filter(
            unit__property=property_obj,
            status=Tenancy.STATUS_ACTIVE,
            start_date__lte=month_end(period_start),
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=period_start))
        .select_related("unit", "tenant")
        .order_by("unit__code", "id")
    )
    rows = []
    for tenancy in tenancies:
        unit = tenancy.unit
        utility = (
            unit.utilities.filter(
                utility_type=UtilityConfiguration.UTILITY_WATER,
                billing_method=UtilityConfiguration.METHOD_METERED,
                effective_from__lte=period_start,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=period_start))
            .order_by("-effective_from", "-id")
            .first()
        )
        if utility is None:
            continue
        meter = str(utility.metadata.get("meter") or unit.meters.get("water") or "").strip()
        if not meter:
            continue
        published = MeterReading.objects.filter(
            unit=unit,
            meter=meter,
            period=period_start,
        ).first()
        previous = MeterReading.objects.filter(
            unit=unit,
            meter=meter,
            period__lt=period_start,
        ).order_by("-period", "-submitted_at", "-id").first()
        draft = MeterReadingDraft.objects.filter(
            unit=unit,
            meter=meter,
            period=period_start,
        ).first()
        previous_value = (
            published.previous_value
            if published
            else draft.previous_value
            if draft
            else previous.current_value
            if previous
            else tenancy.base_water_meter_reading
            if tenancy.base_water_meter_reading is not None
            else unit.meters.get("water_reading")
        )
        baseline_value = (
            tenancy.base_water_meter_reading
            if tenancy.base_water_meter_reading is not None
            else unit.meters.get("water_reading")
        )
        rows.append(
            {
                "unit_id": unit.pk,
                "unit_code": unit.code,
                "meter": meter,
                "previous_value": str(previous_value) if previous_value is not None else None,
                "current_value": (
                    str(published.current_value)
                    if published
                    else str(draft.current_value)
                    if draft
                    else None
                ),
                "status": "published" if published else "draft" if draft else "pending",
                "photo_uploaded": bool(
                    published.photo_key if published else draft.photo_key if draft else ""
                ),
                "baseline_value": (
                    str(baseline_value)
                    if baseline_value is not None
                    else None
                ),
            }
        )
    return rows


def save_caretaker_water_reading_draft(
    actor,
    property_obj,
    period_start,
    unit_id,
    current_value,
    supplied_previous=None,
):
    rows = {
        row["unit_id"]: row
        for row in caretaker_water_units(property_obj, period_start)
    }
    if unit_id not in rows:
        raise WaterReadingError(
            "Unit not found",
            "The unit is unavailable or does not require a meter reading.",
            "unit-not-found",
            404,
        )
    row = rows[unit_id]
    if row["status"] == "published":
        raise WaterReadingError(
            "Reading already published",
            f"Unit {row['unit_code']} is already published for this period.",
            "reading-already-published",
            409,
        )
    known_previous = (
        Decimal(row["previous_value"])
        if row["previous_value"] is not None
        else None
    )
    if (
        known_previous is not None
        and supplied_previous is not None
        and supplied_previous != known_previous
    ):
        raise WaterReadingError(
            "Invalid previous reading",
            f"Unit {row['unit_code']} previous reading must be {known_previous}.",
            "invalid-previous-reading",
        )
    previous_value = known_previous if known_previous is not None else supplied_previous
    if previous_value is None:
        raise WaterReadingError(
            "Baseline required",
            f"Enter the previous meter value for unit {row['unit_code']}.",
            "baseline-required",
        )
    draft = MeterReadingDraft(
        unit_id=unit_id,
        meter=row["meter"],
        period=period_start,
        previous_value=previous_value,
        current_value=current_value,
        submitted_by=actor,
    )
    try:
        draft.clean()
    except ValidationError as exc:
        raise WaterReadingError(
            "Invalid meter reading",
            str(exc.message_dict),
            "invalid-meter-reading",
        ) from exc

    old_photo_key = ""
    with transaction.atomic():
        Property.objects.select_for_update().get(pk=property_obj.pk)
        current_row = next(
            (
                item
                for item in caretaker_water_units(property_obj, period_start)
                if item["unit_id"] == unit_id
            ),
            None,
        )
        if current_row is None:
            raise WaterReadingError(
                "Unit not found",
                "The unit is unavailable or does not require a meter reading.",
                "unit-not-found",
                404,
            )
        if current_row["status"] == "published":
            raise WaterReadingError(
                "Reading already published",
                f"Unit {current_row['unit_code']} is already published for this period.",
                "reading-already-published",
                409,
            )
        current_previous = (
            Decimal(current_row["previous_value"])
            if current_row["previous_value"] is not None
            else None
        )
        if (
            current_previous is not None
            and supplied_previous is not None
            and supplied_previous != current_previous
        ):
            raise WaterReadingError(
                "Invalid previous reading",
                f"Unit {current_row['unit_code']} previous reading must be {current_previous}.",
                "invalid-previous-reading",
            )
        previous_value = (
            current_previous if current_previous is not None else supplied_previous
        )
        if previous_value is None:
            raise WaterReadingError(
                "Baseline required",
                f"Enter the previous meter value for unit {current_row['unit_code']}.",
                "baseline-required",
            )
        draft.meter = current_row["meter"]
        draft.previous_value = previous_value
        try:
            draft.clean()
        except ValidationError as exc:
            raise WaterReadingError(
                "Invalid meter reading",
                str(exc.message_dict),
                "invalid-meter-reading",
            ) from exc
        saved_draft = MeterReadingDraft.objects.select_for_update().filter(
            unit_id=unit_id,
            meter=current_row["meter"],
            period=period_start,
        ).first()
        if saved_draft is None:
            draft.save()
            saved_draft = draft
        else:
            values_changed = (
                saved_draft.previous_value != draft.previous_value
                or saved_draft.current_value != draft.current_value
            )
            old_photo_key = saved_draft.photo_key if values_changed else ""
            saved_draft.previous_value = draft.previous_value
            saved_draft.current_value = draft.current_value
            saved_draft.submitted_by = actor
            if values_changed:
                saved_draft.photo_key = ""
                saved_draft.photo_metadata = {}
            saved_draft.save()
        if old_photo_key:
            transaction.on_commit(lambda: _delete_evidence(old_photo_key))

    record_audit_event(
        None,
        "meter_reading.draft_saved",
        "MeterReadingDraft",
        actor=actor,
        new_values={
            "property_id": property_obj.pk,
            "unit_id": unit_id,
            "meter": saved_draft.meter,
            "period": period_start.isoformat(),
            "previous_value": str(saved_draft.previous_value),
            "current_value": str(saved_draft.current_value),
        },
    )
    return saved_draft


def attach_caretaker_water_reading_photo(
    actor,
    property_obj,
    period_start,
    unit_id,
    content,
    content_type,
    expected_current_value=None,
    source="whatsapp",
):
    if not isinstance(content, bytes):
        raise WaterReadingError(
            "Invalid evidence",
            "Send a valid JPEG, PNG, or WebP meter photo (maximum 5 MB).",
            "invalid-evidence",
        )
    signatures = (
        (b"\xff\xd8\xff", "image/jpeg", "jpg"),
        (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    )
    match = next(
        (
            (mime_type, extension)
            for signature, mime_type, extension in signatures
            if content.startswith(signature)
        ),
        None,
    )
    if match is None and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        match = ("image/webp", "webp")
    if (
        match is None
        or (content_type and content_type not in {match[0], "application/octet-stream"})
        or not content
        or len(content) > MAX_METER_PHOTO_BYTES
    ):
        raise WaterReadingError(
            "Invalid evidence",
            "Send a valid JPEG, PNG, or WebP meter photo (maximum 5 MB).",
            "invalid-evidence",
        )
    mime_type, extension = match
    row = next(
        (
            item
            for item in caretaker_water_units(property_obj, period_start)
            if item["unit_id"] == unit_id
        ),
        None,
    )
    if row is None:
        raise WaterReadingError(
            "Unit not found",
            "The unit is unavailable or does not require a meter reading.",
            "unit-not-found",
            404,
        )
    draft = MeterReadingDraft.objects.filter(
        unit_id=unit_id,
        meter=row["meter"],
        period=period_start,
    ).first()
    if draft is None:
        raise WaterReadingError(
            "Reading not saved",
            "Save the current meter reading before uploading its photo.",
            "reading-not-saved",
            409,
        )
    expected_values = (draft.previous_value, draft.current_value)
    if (
        expected_current_value is not None
        and draft.current_value != expected_current_value
    ):
        raise WaterReadingError(
            "Reading changed",
            "The saved reading changed. Reopen this unit and try again.",
            "reading-changed",
            409,
        )
    storage_key = (
        f"water-meter-readings/{property_obj.pk}/{period_start.isoformat()}/"
        f"{unit_id}/{uuid.uuid4().hex}.{extension}"
    )
    try:
        upload_private_object(storage_key, content, mime_type)
        with transaction.atomic():
            Property.objects.select_for_update().get(pk=property_obj.pk)
            locked_draft = MeterReadingDraft.objects.select_for_update().filter(
                pk=draft.pk,
            ).first()
            if (
                locked_draft is None
                or (
                    locked_draft.previous_value,
                    locked_draft.current_value,
                )
                != expected_values
            ):
                raise WaterReadingError(
                    "Reading changed",
                    "The saved reading changed before its photo could be attached. Save it again and retry.",
                    "reading-changed",
                    409,
                )
            old_storage_key = locked_draft.photo_key
            locked_draft.photo_key = storage_key
            locked_draft.photo_metadata = {
                "content_type": mime_type,
                "size_bytes": len(content),
                "name": f"Meter evidence.{extension}",
            }
            locked_draft.full_clean()
            locked_draft.save(update_fields=["photo_key", "photo_metadata", "updated_at"])
            record_audit_event(
                None,
                "meter_reading.evidence_uploaded",
                "MeterReadingDraft",
                actor=actor,
                new_values={
                    "unit_id": unit_id,
                    "property_id": property_obj.pk,
                    "period": period_start.isoformat(),
                    "size_bytes": len(content),
                    "content_type": mime_type,
                    "source": source,
                },
            )
    except WaterReadingError:
        _delete_evidence(storage_key)
        raise
    except (BotoCoreError, ClientError, ImproperlyConfigured, OSError):
        _delete_evidence(storage_key)
        logger.exception("Caretaker water meter evidence upload failed.")
        raise WaterReadingError(
            "Evidence storage unavailable",
            "The meter photo could not be stored. Please try again.",
            "meter-evidence-unavailable",
            503,
        ) from None
    except ValidationError as exc:
        _delete_evidence(storage_key)
        raise WaterReadingError(
            "Invalid evidence",
            str(exc.message_dict),
            "invalid-evidence",
        ) from exc
    except DatabaseError:
        _delete_evidence(storage_key)
        logger.exception("Could not save caretaker water meter evidence metadata.")
        raise
    if old_storage_key:
        transaction.on_commit(lambda: _delete_evidence(old_storage_key))
    return locked_draft


def publish_caretaker_water_readings(property_obj, period_start, actor, request=None):
    prepared_readings = []
    drafts_to_delete = []
    with transaction.atomic():
        Property.objects.select_for_update().get(pk=property_obj.pk)
        rows = caretaker_water_units(property_obj, period_start)
        if not rows:
            return {
                "error": (
                    409,
                    "No readings required",
                    "There are no occupied units requiring water readings.",
                    "no-readings-required",
                )
            }
        missing = [
            row["unit_code"]
            for row in rows
            if row["status"] != "published"
            and (
                row["previous_value"] is None
                or row["current_value"] is None
                or not row["photo_uploaded"]
            )
        ]
        if missing:
            return {
                "error": (
                    409,
                    "Readings incomplete",
                    "Save a valid reading and meter photo for every occupied unit before publishing: "
                    f"{', '.join(missing)}.",
                    "readings-incomplete",
                )
            }
        if all(row["status"] == "published" for row in rows):
            return {
                "error": (
                    409,
                    "Readings already published",
                    "All water readings for this period have already been published.",
                    "readings-already-published",
                )
            }
        for row in rows:
            if row["status"] == "published":
                continue
            draft = MeterReadingDraft.objects.select_for_update().filter(
                unit_id=row["unit_id"],
                meter=row["meter"],
                period=period_start,
            ).first()
            if draft is None:
                return {
                    "error": (
                        409,
                        "Readings incomplete",
                        f"Unit {row['unit_code']} has no saved draft.",
                        "readings-incomplete",
                    )
                }
            reading = MeterReading(
                unit_id=row["unit_id"],
                meter=row["meter"],
                period=period_start,
                previous_value=draft.previous_value,
                current_value=draft.current_value,
                submitted_by=draft.submitted_by,
                photo_key=draft.photo_key,
                photo_metadata=draft.photo_metadata,
            )
            try:
                reading.full_clean()
            except ValidationError as exc:
                return {
                    "error": (
                        400,
                        "Invalid meter reading",
                        str(exc.message_dict),
                        "invalid-meter-reading",
                    )
                }
            prepared_readings.append(reading)
            drafts_to_delete.append(draft)
        MeterReading.objects.bulk_create(prepared_readings)
        MeterReadingDraft.objects.filter(
            pk__in=[draft.pk for draft in drafts_to_delete]
        ).delete()
        for reading in prepared_readings:
            record_audit_event(
                request,
                "meter_reading.published",
                "MeterReading",
                actor=actor,
                new_values={
                    "unit_id": reading.unit_id,
                    "meter": reading.meter,
                    "period": reading.period.isoformat(),
                    "previous_value": str(reading.previous_value),
                    "current_value": str(reading.current_value),
                    "submitted_by": reading.submitted_by_id,
                    "photo_metadata": reading.photo_metadata,
                },
            )

    try:
        from users.tasks import draft_property_invoices

        draft_property_invoices.delay(property_obj.pk, period_start.isoformat())
    except BrokerOperationalError:
        logger.exception(
            "Published caretaker readings but could not queue invoice drafting for property %s, period %s.",
            property_obj.pk,
            period_start,
        )
        return {
            "published": len(prepared_readings),
            "period": period_start.isoformat(),
            "invoice_drafting_queued": False,
        }
    return {
        "published": len(prepared_readings),
        "period": period_start.isoformat(),
        "invoice_drafting_queued": True,
    }
