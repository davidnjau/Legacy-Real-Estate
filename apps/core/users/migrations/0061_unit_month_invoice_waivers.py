from django.db import migrations, models
import django.db.models.deletion


def backfill_waiver_context(apps, schema_editor):
    WaiverRequest = apps.get_model("users", "InvoiceWaiverRequest")
    ArrearsWaiver = apps.get_model("users", "ArrearsWaiver")
    database = schema_editor.connection.alias

    for request in WaiverRequest.objects.using(database).select_related(
        "invoice__tenancy__unit"
    ).iterator():
        if request.invoice_id is None:
            raise RuntimeError(
                f"Invoice waiver request {request.pk} has no invoice to backfill."
            )
        request.unit_id = request.invoice.tenancy.unit_id
        request.period_start = request.invoice.period_start
        request.save(update_fields=["unit", "period_start"])

    for request in WaiverRequest.objects.using(database).filter(
        status="approved",
        invoice__isnull=False,
    ).iterator():
        ledger_waiver = (
            ArrearsWaiver.objects.using(database)
            .filter(
                invoice_id=request.invoice_id,
                amount=request.amount,
                reason=request.reason,
                approved_by_id=request.decided_by_id,
                invoice_waiver_request__isnull=True,
            )
            .order_by("id")
            .first()
        )
        if ledger_waiver:
            ledger_waiver.invoice_waiver_request_id = request.pk
            ledger_waiver.save(update_fields=["invoice_waiver_request"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0060_propertyunittype_unit_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoicewaiverrequest",
            name="unit",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoice_waiver_requests",
                to="users.unit",
            ),
        ),
        migrations.AddField(
            model_name="invoicewaiverrequest",
            name="period_start",
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name="arrearswaiver",
            name="invoice_waiver_request",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ledger_waiver",
                to="users.invoicewaiverrequest",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="invoicewaiverrequest",
            name="users_invoice_one_pending_waiver_unique",
        ),
        migrations.RunPython(backfill_waiver_context, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invoicewaiverrequest",
            name="invoice",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="waiver_requests",
                to="users.invoice",
            ),
        ),
        migrations.AlterField(
            model_name="invoicewaiverrequest",
            name="unit",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoice_waiver_requests",
                to="users.unit",
            ),
        ),
        migrations.AlterField(
            model_name="invoicewaiverrequest",
            name="period_start",
            field=models.DateField(),
        ),
        migrations.AddIndex(
            model_name="invoicewaiverrequest",
            index=models.Index(
                fields=["unit", "period_start"],
                name="users_waiver_unit_period_idx",
            ),
        ),
    ]
