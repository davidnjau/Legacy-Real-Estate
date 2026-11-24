# Tenant portal

The tenant dashboard reads from Django and PostgreSQL. `GET /api/v1/tenant/dashboard`
requires an authenticated account in the `Tenant (portal)` role linked to a
tenant record; it returns only that tenant's issued invoices, payment records,
receipts, and current active tenancy context. Dashboard views are audit logged.
The response also includes the active tenancy's deposit balance and entries,
the configured notice period, and move-out settlement refund status for the
linked tenant only. The tenant UI reads these records without offering any
deposit or refund mutation controls.

## Move-in reservations and tenant activation

The landlord unit and move-in settlement routes now use Django sessions and
PostgreSQL:

- `GET /api/v1/landlord/move-in-units` returns the active property's unit,
  tenancy, and open-reservation summary.
- `GET`, `POST`, `PATCH`, and `DELETE
  /api/v1/landlord/move-in-settlements` list, create, collect payment for, send
  activation for, or cancel a move-in reservation.
- Reservation totals snapshot the active `PropertyRecurringCharge` records at
  creation. Payments are recorded as `MoveInPayment` entries and applied under
  a database lock; overpayments are rejected. The unit becomes booked once the
  security and water-deposit target is paid.
- A tenant record and active tenancy are created only after the full move-in
  balance and deposits are cleared. New tenants provide their national ID or
  passport during account activation. Until then, the tenant record is marked
  provisional and excluded from normal tenant lists.
- Move-in M-Pesa attempts are stored in PostgreSQL and callbacks are matched by
  checkout request ID, amount, and receipt reference. Mismatches are left in
  `needs_review` and do not increase the settlement balance.
- Property Officer onboarding creates the tenant, tenancy, move-in package,
  and invoice before collecting payment. The tenant activation email is sent
  only after confirmed allocations cover the full move-in invoice; pending or
  bounced cheques are excluded. The email and queued SMS contain the same
  one-time activation link and six-digit code, both required to set a password.
  Activation does not sign the tenant in; they continue to the login page.
- A new tenancy reserves its unit as `Booked`. The unit changes to `Occupied`
  only after confirmed payment allocations cover the complete move-in invoice
  and the tenancy start date has arrived. `GET
  /api/v1/tenancies/{tenancy_id}/move-in-status` reports confirmed and recorded
  payment totals and refreshes this transition. Pending and bounced
  instruments remain excluded from confirmed totals.
- Tenant documents can be uploaded with
  `POST /api/v1/tenants/{tenant_id}/documents` as multipart form data containing
  `document_type`, `name`, `file`, and optionally `tenancy_id`. Supported types
  are `signed_agreement`, `identity_document`, `proof_of_payment`, and `other`.
  PDF, JPEG, PNG, and WebP files up to 10 MB are accepted; the server validates
  file signatures, generates the private storage key, and returns a scoped
  `download_path` rather than the key. Authorized staff download files through
  `GET /api/v1/tenants/{tenant_id}/documents/{document_id}`.
- Local and staging Compose run `sync_move_in_occupancy` immediately after the
  core service is ready and then every 24 hours. The one-shot command can also
  be run manually with `python manage.py sync_move_in_occupancy`.

Tenant profile, tutorial/onboarding, and unit-status APIs use Django session
authentication and read the linked PostgreSQL `Tenant`, `Tenancy`, and `Unit`
records. A unit cannot be marked vacant while an active tenancy still exists;
staff must use the audited move-out workflow.

Invoice balances subtract valid payment allocations and approved arrears waivers
as of the current local date, then include any applicable overdue penalty.
Pending-clearance and bounced instruments are not counted as paid. Invoice lines
are shown separately from the total, while receipt numbers and payment
references are limited to the linked tenant's own records.
The tenant dashboard presents the invoice/payment history, receipt references,
deposit ledger movements, and any move-out refund settlement status from these
PostgreSQL records; it does not substitute sample records when an API request
fails.

## WhatsApp account linking

Tenants can start a WhatsApp session from the dashboard's **Chat on WhatsApp**
link or by messaging the business number, +254 183 666 549. An unlinked contact
is prompted for the tenant's national ID or company registration number. After
verification, select **Tenant** if asked to choose a role, then select a
service from the WhatsApp menu.

## Rent STK payments

The tenant portal and WhatsApp rent-payment flows use Safaricom Daraja STK Push.
Configure `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`,
`MPESA_PASSKEY`, and `MPESA_BASE_URL` in the core service environment. The
credentials must be issued for the Daraja app and shortcode; NCBA banking
username, password, secret key, and account number are not substitutes.
`MPESA_BASE_URL` defaults to the Safaricom sandbox URL. Use the production URL
only after the Daraja app and shortcode are approved for live payments.

The callback must be reachable over HTTPS from Safaricom. Set
`MPESA_RENT_CALLBACK_URL` explicitly, or set `PUBLIC_SITE_URL` so the app can
derive `/api/v1/payments/mpesa/rent-stk-callback`. Successful callbacks are
matched to the pending STK request, merchant request, amount, payer phone, and
invoice before recording the payment, allocation, and receipt. Tenants enter
the number for each portal payment. WhatsApp automatically targets the latest
outstanding invoice, asks for the Safaricom number, then asks whether to pay
the full remaining balance or a custom whole-shilling amount. The portal also
shows available rent credit and allows full, partial, or extra payments.
Available tenant credit reduces the suggested full-payment amount and is
allocated to the selected invoice; any payment amount beyond its remaining
balance is recorded as credit for a later invoice. Confirmed payments queue an
SMS receipt to the tenant's registered phone. Sandbox tests simulate payments;
production payments debit real funds and are not automatically refunded.

## Legacy data import

Property Officers register property profiles first; registration stores the
expected unit count and reusable unit-type definitions, including each type's
monthly rent, security deposit, and fixed or metered utilities. Metered water
can include an optional utility deposit. These definitions do not create units
or change existing unit charges. Existing properties can use a saved legacy
default type and rent until their inventory is migrated. Unit inventory is
imported from the property's **Units & vacancies** view using the Agency
Operations **Legacy data import** page. The import is bound to the selected
property and cannot create properties.

A property-scoped XLSX workbook contains two worksheets, `Units` and
`Tenancies`; CSV uploads use `units.csv` and `tenancies.csv`. Tenancies may
contain only the occupied units being imported. Every occupied unit must have
one matching `unit_code` row in Tenancies. Vacant units need no tenancy row.

| Sheet / CSV name | Required columns | Optional columns |
|---|---|---|
| `Units` | `unit_code` (or `code`), `unit_type`, `occupancy_status` (or `status`) | `unit_label`, `block_name`, `floor_name`, `floor_level`, `bedrooms`, `effective_from`, `water_meter`, `water_reading_period`, `water_current_reading`, `water_previous_reading` |
| `Tenancies` | `unit_code`, `tenant_name`, `tenant_phone`, `tenant_email`, `tenancy_start_date` | `tenant_id_number`, `tenant_kra_pin`, `tenant_type`, `company_registration_number`, `contact_person`, `tenancy_end_date`, `tenancy_type`, `due_day`, `base_water_reading`, `opening_arrears`, `arrears_as_of` |

`unit_type` must match a type saved on the selected property. The import uses
that definition to create the unit's rent charge, fixed recurring utility
charges, and metered-water configuration; it does not accept a rent override.
Metered-water types require a `water_meter` identifier for each imported unit.
The optional latest reading requires both `water_reading_period` and
`water_current_reading`; the period accepts `YYYY-MM` or the first day of that
month. `water_previous_reading` is optional and defaults to the current reading
when no prior value is known, establishing a zero-usage baseline. The tenancy's
optional `base_water_reading` is kept separately as the move-in baseline.

If `opening_arrears` is greater than zero, `arrears_as_of` is required. The
import records it as an opening migration invoice/balance; it does not import
historical payments, invoices, or deposit transactions. Payments made through
the platform are recorded by the normal payment flow. The security and utility
deposit definitions set the tenancy's required deposit target, with an opening
deposit balance of zero unless recorded through the supported deposit workflow.
Property Officers can review these balances in the **Migration ledger**
(`/property-officer/ledger`). It shows only imported opening arrears and reduces
the outstanding amount by confirmed payment allocations and approved waivers.
Property and payment-status filters are branch-scoped, and the ledger refreshes
periodically while open.

On commit, occupied-tenant rows provision inactive tenant portal accounts and
send unique activation links and six-digit codes by email and queued SMS after
the import transaction commits. Preview performs a transactional dry run and
sends no messages. Email delivery and SMS queueing are reported separately;
failed delivery is reported per channel. Re-running the import reissues a fresh
challenge for an inactive tenant account; active accounts are not re-invited.
Existing unit/tenancy records are skipped and never overwritten. Invalid rows
reject the entire batch atomically.
If the property has default handover inventory, each newly imported tenancy
receives a legacy handover record with every configured item and quantity
recorded as issued; properties without defaults create no handover record. A
tenant without an identity document remains provisional until they provide it
during account activation. After opening the link, the tenant must enter the
six-digit OTP, create and confirm a password, then sign in to access the tenant
portal.

Published branch-wide and current-property announcements are served through
`GET /api/v1/announcements`, which applies the same tenant session and tenancy
scope. Draft, expired, and unrelated property announcements are not visible to
tenants.

## Maintenance requests

The tenant maintenance portal uses Django endpoints:

- `GET /api/v1/tenant/maintenance-tickets` lists tickets reported by the signed-in tenant.
- `POST /api/v1/tenant/maintenance-tickets/create` creates a request for the
  tenant's active tenancy. The API derives the property and unit server-side;
  client-supplied property or unit identifiers are not used.
- `GET` and `POST /api/v1/tenant/maintenance-tickets/{ticket_id}/comments`
  return tenant-visible conversation entries or add a tenant reply. Tickets
  reported by another account are not accessible.
- `GET /api/v1/tenant/maintenance-tickets/{ticket_id}/evidence/{index}` streams
  an evidence image only to the tenant who reported the ticket.

Evidence is optional and accepts up to five JPEG, PNG, or WebP images, 5 MB
each and 20 MB total. The backend checks the image signatures and stores files
in a private S3-compatible bucket; it does not expose object URLs. Authorized
agency operations staff, including Property Officers, can manage tickets for
properties within their assigned agency branches. Landlords can view requests
for their own properties, while assigned caretakers handle field work and
advance tickets through the maintenance lifecycle. These roles can retrieve
authorized evidence through authenticated endpoints and reply in the same
conversation. Staff must record the technician and actual repair cost before
completing a ticket; completed repair records feed the landlord and caretaker
maintenance ledger in Django. Evidence uploads, reads, ticket creation, tenant
list reads, and tenant replies are audited. Comments are tenant-visible
because the current comment model has no private/internal visibility flag;
staff should not place internal-only notes in this conversation.

Local Compose creates the private `legacy-private` bucket on first upload.
For staging, create that bucket as private in MinIO and configure
`OBJECT_STORAGE_ACCESS_KEY` and `OBJECT_STORAGE_SECRET_KEY` with a
bucket-scoped service account that can read, write, and delete objects under
all active upload prefixes:

- `maintenance/tenant/` (tenant portal and WhatsApp maintenance evidence)
- `tenant-documents/`
- `move-in/`
- `water-meter-readings/`
- `maintenance-tickets/completion/`
- `inspections/move-out/`

Keep these credentials server-side and separate from the MinIO root account.
The application does not create buckets in staging.

## Move-out notices

Tenants with an active tenancy can review or submit a proposed move-out date
from the dashboard's **Move-out notice** section:

- `GET /api/v1/tenant/move-out-notice` returns the active tenancy, notice
  period, and current notice. It also returns the tenant's active tenancy
  choices; pass `?tenancy_id={tenancy_id}` to select one.
- `POST /api/v1/tenant/move-out-notice/create` submits a proposed date and
  optional reason. Include `tenancy_id` when the tenant has multiple active
  tenancies; the server enforces the tenancy's configured notice period.
- `POST /api/v1/tenant/move-out-notices/{notice_id}/withdraw` withdraws a
  pending notice. Accepted notices must be withdrawn by authorized property
  staff.

Recording a notice moves its unit to **Notice Given** immediately. A rejection
or withdrawal restores the prior tenancy end date and unit status. The tenant,
scoped property-management staff, and assigned caretaker receive SMS
notifications at notice and inspection milestones.

When a Property Officer accepts a notice, they assign an active caretaker in
the same operation. The system creates a pending exit inspection for the
proposed move-out date and notifies the caretaker and tenant by SMS. The
caretaker completes the exit checklist and captures final readings, damage
estimates, findings, and photo evidence. Property staff then prepare a
settlement that itemizes the deposit held, invoice arrears, final metered
utilities, damage deductions, refund, and any debt shortfall. Submitting the
settlement moves the unit to **Deposit Pending** and makes the tenant's move-out
tracker read-only for that unit; the tenancy is retained and is not closed
until payout is recorded. Other active tenancies on the same tenant account
remain usable.

The ordinary move-out-month invoice includes the regular metered-water charge
from the published monthly reading, but excludes rent and fixed monthly
utilities for that month. Any metered usage from the regular reading through
the caretaker's final reading is deducted from the deposit; it is not issued
as a separate invoice.

A different Branch Manager must approve the settlement. An
Accountant with finance MFA records the manual payout reference and date and
the deposit-ledger movements. On completion, the tenancy is closed and the unit
becomes **Vacant** or **Maintenance** according to the inspection. The tracker
shows inspection, ledger reconciliation, approval, refund status, itemized
calculations, and authorized inspection photos. Download the final statement
with `GET /api/v1/tenant/move-out-settlements/{settlement_id}/statement`; the
payout SMS also includes its link. If the settlement has a shortfall, the tenant can use the
property Paybill with the vacated unit code as the account reference or contact
the Property Officer to arrange debt clearance. Historical tenant and financial
records are retained.
