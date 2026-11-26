# Property records

Wave 1 property records are branch-scoped and available to the landlord-management
roles: Super Admin (Director), Branch Manager, Accountant, and Property Officer /
Agent. A user can only read or create records for branches available through their
agency membership. Directors can access every branch in their own agency;
other staff are restricted to branches assigned in that membership. Out-of-scope
properties return `404` rather than revealing that a record exists.

Each property can reference a landlord whose home branch may differ from the
property branch. Property access remains scoped to the property's branch; the
landlord portal lists all properties linked to that landlord account. A
property can also include:

- property type, location, GPS coordinates, amenities, house rules, and photo
  references;
- optional move-in handover inventory items and their expected quantities; the
  list can remain empty for properties with nothing to issue;
- one or more caretakers, who must have the `Caretaker` role and branch access;
- blocks and floors, with unique block names per property and unique floor levels
  per block; and
- document metadata, including a storage key, document type, and arbitrary
  metadata.

## Landlord property and unit lists

The property and unit list endpoints enforce role-based filtering:

- `GET /api/v1/properties`
- `GET /api/v1/units`

Staff receive properties and units in their assigned branches, caretakers
receive records for assigned properties, and landlord portal users receive all
records linked to their landlord account, including properties across branches.
Landlords cannot read another landlord's records.

Explicit landlord list routes are also available:

- `GET /api/v1/landlord/properties`
- `GET /api/v1/landlord/units`

These routes enforce ownership on the server and audit successful reads. Super
Admin (Director) can access all branches in their agency, while other authorized
staff are limited to branches assigned in their agency membership.

The landlord property endpoint also supports:

- `POST /api/v1/landlord/properties` to create a property under the signed-in
  landlord's linked landlord record. Branch and landlord ownership come from
  that record, not from caller-supplied IDs. Property defaults and up to 20
  recurring charges are saved atomically. The new property becomes active if
  the account has no active property linked to it.
- `PATCH /api/v1/landlord/properties` with `{"activePropertyId": <id>}` to
  select an owned property, or `{"propertyId": <id>, ...}` to update its
  configuration. Supplying `recurringCharges` replaces that property's
  recurring-charge list atomically; omitting it leaves the list unchanged.

Property configuration and recurring charges are stored in PostgreSQL. Writes
are scoped to the authenticated landlord's linked records and audit logged.
Recurring-charge names are unique per property without regard to case. Monetary
values must be non-negative; rent due day must be between 1 and 28.

## Landlord oversight dashboard

The landlord portal dashboard is available through
`GET /api/v1/landlord/dashboard`. It requires an authenticated
`Landlord (portal)` account and returns only properties linked to that user's
landlord record. Successful reads are recorded as immutable
`landlord_dashboard.viewed` audit events; the dashboard is read-only.

The response includes portfolio totals and a per-property breakdown for the
current calendar month in the configured Nairobi timezone:

- Occupancy counts non-archived units and counts units in the occupied status.
- Expected rent & charges is the total of issued invoices whose billing period
  starts in the current month. Invoice-level payment allocations are not split
  by charge line, so collected rent & charges is the valid amount allocated to
  those invoices; pending-clearance and bounced cheque allocations are excluded.
- Arrears are balances on issued invoices due before today, after valid
  allocations and approved waivers.
- Approved and pending expenses include records whose effective date starts in
  the current month. Rejected expenses are excluded.
- Open maintenance counts tickets that are neither completed nor closed.

Money values are decimal strings in KES. If no properties are linked, the
endpoint returns zero-valued portfolio totals and an empty property list.

Caretaker accounts and property assignments are managed by authorized property
staff through:

- `POST /api/v1/caretakers/create`
- `GET /api/v1/properties/{property_id}/caretakers`
- `POST /api/v1/properties/{property_id}/caretakers/assign`
- `POST /api/v1/properties/{property_id}/caretakers/{caretaker_id}/end`

Caretaker creation requires an email, display name, password of at least 12
characters, and one or more accessible branches. Assignment requires an active
user with the `Caretaker` role and access to the property's branch. Assignments
retain their start and end timestamps and are audited; ending an assignment
also removes the legacy property caretaker relation. Caretakers can only view
properties with an active assignment.

The caretaker dashboard is available through `GET /api/v1/caretaker/dashboard`.
The caretaker property selector uses `GET` and `PATCH /api/v1/caretaker/properties`
to list assignments and persist the active property. Caretaker portal data is
scoped to that selected property; switching properties refreshes the dashboard,
readings, checklists, inspections, and maintenance views.

The dashboard returns the selected property's unit status counts, the current
month's reading status, recent readings, and pending move-in checklist items.
Maintenance tickets in the selected property's scope are included as well.

Authorized property staff can invite a caretaker to one or more accessible
properties in a single registration. The invitation's activation creates one
caretaker account and an active assignment for each selected property.

Move-in handovers are submitted through `GET /api/v1/caretaker/checklists`,
`POST /api/v1/caretaker/checklists/{package_id}/photos`, and
`POST /api/v1/caretaker/checklists/{package_id}/submit`. The submission records
keys issued, a room/fixture condition checklist, opening readings for configured
metered water and electricity, and private photo evidence. Water readings become
the tenancy's opening billing baseline after Property Officer confirmation.
Property Officer onboarding assigns one active caretaker to the task and queues
an SMS immediately. The pending task is visible before the move-in date, but
photo uploads and checklist submission are enabled only on or after that date.
Submitted handovers are reviewed through
`GET /api/v1/property-officer/move-in-handovers` and confirmed through
`POST /api/v1/property-officer/move-in-handovers/{package_id}/confirm`.
Historical move-in packages are marked legacy so they do not appear as new work.

Caretakers can submit readings in one atomic mobile-friendly request through
`POST /api/v1/caretaker/meter-readings`. The request accepts 1–250 readings,
validates that every unit is currently assigned to the caretaker, rejects
duplicate meter-period entries, and records an audit event for each reading.

Monthly metered-water readings are collected separately through the caretaker
portal or WhatsApp. Both flows guide the caretaker through one occupied unit at
a time: enter the current value, capture and save a photo of that unit's meter,
then continue to the next unit. The portal requests the device camera and keeps
unfinished drafts available to resume. Publishing requires a saved reading and
meter photo for every eligible unit; the portal and WhatsApp both require a
final publish confirmation, after which invoice drafting is queued.
The portal wizard stays visible between reading windows and shows each unit's
previous reading, but entry, photo uploads, and publication stay locked until
the 30th of the month, or February 27 in a common year / February 28 in a leap
year. The API enforces the same opening date.

Maintenance tickets are managed through:

- `GET /api/v1/maintenance-tickets`
- `POST /api/v1/maintenance-tickets/create`
- `POST /api/v1/maintenance-tickets/{ticket_id}/status`
- `POST /api/v1/maintenance-tickets/{ticket_id}/comments`

Tickets support category, priority, photo metadata, auditable comments, and the
SRS lifecycle from `open` through `closed`. Caretakers can create tickets and
update tickets assigned to them; authorized property staff can manage tickets
within their branch scope. Tenant self-service submission is intentionally
handled by the later W4 tenant-maintenance workflow.

The caretaker ticket board is available at `/caretaker/tickets`; property-wide
reports are supported when an issue is in a common area. Assigned caretakers
must attach private completion photos before submitting a completed ticket.
Completion queues SMS updates for scoped property staff and, when the ticket is
for an occupied unit, the active tenant.

Verified caretaker WhatsApp contacts can also view tickets in their active
assigned-property scope, claim open unassigned tickets, start their assigned
work, view and reply to ticket comments, and report property-wide on-site issues.
Completing an in-progress ticket requires the technician/fundi name, a
non-negative repair cost, and at least one JPEG, PNG, or WebP completion photo.
Ticket actions are audited; completion queues the same staff and tenant SMS
notifications as the portal and a tenant WhatsApp notification when enabled.

The vendor/fundi registry is managed by authorized property staff through:

- `GET /api/v1/vendors`
- `POST /api/v1/vendors/create`
- `POST /api/v1/vendors/{vendor_id}`

Vendor records are branch-scoped and store trade, phone, M-Pesa number, email,
rating, notes, and active status. Tickets can reference an active vendor from
the same property branch. Vendor records are retained when deactivated so
future quotation and expense workflows can preserve historical references.

Maintenance quotations and cost allocations are managed through:

- `GET /api/v1/maintenance-tickets/{ticket_id}/quotations`
- `POST /api/v1/maintenance-tickets/{ticket_id}/quotations/create`
- `POST /api/v1/maintenance-quotations/{quotation_id}/approval`
- `POST /api/v1/maintenance-quotations/{quotation_id}/allocations/create`

Quotation approval thresholds are read from the active landlord management
agreement's `terms.maintenance_approval_threshold` value (with
`terms.approval_threshold` accepted as a compatibility alias). Quotations above
the threshold require authorized property staff approval; assigned caretakers
may approve quotations at or below the threshold. Approved quotations can be
allocated to the property landlord or the active tenant, and allocations cannot
exceed the approved quotation amount.

Inspections and landlord-shareable reports are managed through:

- `GET /api/v1/inspections`
- `POST /api/v1/inspections/create`
- `POST /api/v1/inspections/{inspection_id}/complete`
- `POST /api/v1/inspections/{inspection_id}/photos`
- `POST /api/v1/inspections/{inspection_id}/share`
- `GET /api/v1/inspections/{inspection_id}/report`
- `GET /api/v1/inspection-reports/{share_token}`

An inspection records its property or unit, schedule, inspection type,
responsible staff member, checklist results, photo metadata, findings, and
recommendations. Assigned caretakers can complete inspections in their
property scope; authorized management staff can manage all inspections in
their accessible branches. Only completed reports can be shared. Sharing
enables an opaque UUID token, while the public report response excludes that
token and other management-only metadata. Inspection creation, completion, and
sharing are audited.

Verified caretaker WhatsApp contacts can also submit pending move-in handovers
for their actively assigned properties and complete their own scheduled or
in-progress routine inspections. Handovers collect keys and access devices
issued, expected-versus-issued quantities for each configured property
inventory item, every required room/fixture condition and notes for issues,
and opening readings for configured metered utilities. At least one privately
stored photo is required; when water is metered, a separately identified water
meter photo is also required. Photos must be JPEG, PNG, or WebP, up to 10 total
and 5 MB each. The portal and WhatsApp collect the same handover data, and
Property Officers can review the item counts and labelled photos before
confirming. Submission is audited and queues the same scoped staff SMS
notification as the portal. Routine inspection completion records every
checklist item, findings (required when no checklist exists), and optional
recommendations, and is audited. Move-out/exit inspections remain in the
separate caretaker dashboard workflow.

Caretaker WhatsApp sessions retain a selected property. When multiple
properties are assigned, the caretaker selects one before opening services and
can switch properties from the caretaker menu; caretaker tasks and maintenance
records are limited to that property. The menu's occupancy summary excludes
draft and archived units from the total, counts occupied and notice-given units
as occupied, and lists the unit codes currently marked vacant.
The caretaker can also record monthly water readings unit-by-unit, attach a
photo for each meter, review completion for all eligible units, and explicitly
confirm publication from WhatsApp.

Property expenses are managed through:

- `GET /api/v1/property-expenses`
- `POST /api/v1/property-expenses/create`
- `POST /api/v1/property-expenses/{expense_id}/approval`

Expenses may be one-off, monthly, quarterly, or annual. Each record stores its
category, amount, effective period, next due date for recurring expenses, and
optional active vendor. The active landlord management agreement controls the
`terms.expense_approval_threshold` value, with `terms.approval_threshold`
accepted as a compatibility alias. Expenses above that threshold require
authorized property-management approval. The submitter cannot approve the same
expense, and approval or rejection records the actor, timestamp, and reason.
Expense submission and decisions are audited; remittance calculation remains a
later workflow.

Announcements and the tenant notice board are managed through:

- `GET /api/v1/announcements`
- `POST /api/v1/announcements/create`
- `POST /api/v1/announcements/{announcement_id}/publish`

Authorized property-management staff can create branch-wide or
property-specific draft notices and publish them immediately or for a future
time. Notices may have an expiry time. Tenant portal users receive only
currently published, non-expired notices for their branch and active tenancy
property; draft, archived, and unrelated property notices are excluded.
Creation and publication are audited.

Document endpoints store metadata and a storage key only. They do not upload
binary content yet. Binary uploads and signed object-storage URLs will be added
when the agreed MinIO/S3 integration is brought into scope.

The current endpoints are:

- `GET /api/v1/properties`
- `GET /api/v1/properties/{property_id}`
- `POST /api/v1/properties/create`
- `POST /api/v1/properties/{property_id}/blocks`
- `POST /api/v1/blocks/{block_id}/floors`
- `POST /api/v1/properties/{property_id}/documents`

Units are managed through:

- `GET /api/v1/units`
- `GET /api/v1/units/{unit_id}`
- `GET /api/v1/units/{unit_id}/history`
- `POST /api/v1/units/create`
- `POST /api/v1/units/{unit_id}/status`

The vacancy board is available through `GET /api/v1/vacancy-board`. It returns
accessible units with their current effective rent, property location, and
branch. Results can be filtered by `branch_id`, `area`, `unit_type`, `status`,
`listing_enabled`, `rent_min`, and `rent_max`. Branch filters are validated
against the user's access scope, and rent filters use the current effective
rent charge as of the request date.

Unit codes are unique within a property. A unit records its type, bedroom count,
characteristics, meter references, and publication flag. Listing is allowed only
for `Vacant` and `Notice Given` units; the API rejects other combinations.

The supported lifecycle is `Draft`, `Vacant`, `Occupied`, `Notice Given`,
`Maintenance`, and `Archived`. Transitions are validated server-side and every
status change is written to the immutable audit log. Moving a unit out of
`Vacant` or `Notice Given` automatically disables its listing flag.

Unit history returns the unit's preserved tenancies, effective-dated charges,
status-change audit events, and charge audit events. It also returns an empty
`maintenance` array because maintenance tickets are not yet part of the current
Wave 1 data model; later maintenance records can be added without changing the
historical tenancy, charge, or audit data.

Effective-dated unit charges are managed through:

- `GET /api/v1/units/{unit_id}/charges`
- `POST /api/v1/units/{unit_id}/charges/create`

Charges support `rent`, `deposit`, and `recurring` types, with an amount,
effective start date, optional end date, and metadata. A future charge is added
as a new record; the previous open-ended charge is closed the day before it
starts. Historical amounts and their effective periods remain available for
statements and audit review. Overlapping periods for the same charge type are
rejected.

Recurring charges can use separate codes such as `service`, `security`, and
`garbage`, allowing multiple recurring categories for the same unit while
preserving effective-date history.

Utility configurations are managed through:

- `GET /api/v1/units/{unit_id}/utilities`
- `POST /api/v1/units/{unit_id}/utilities/create`

Utilities support fixed or metered billing, effective dates, fixed amounts,
per-unit rates, and minimum charges. Configuration is stored separately from
water usage calculations; usage billing is handled by the later W2 water
calculation task.

Meter readings are managed through:

- `GET /api/v1/units/{unit_id}/meter-readings`
- `POST /api/v1/units/{unit_id}/meter-readings/create`

Each reading stores the meter identifier, period, previous/current values,
submitter, timestamp, and optional image evidence metadata. Current values must
not decrease, readings are unique per unit/meter/period, and evidence is
limited to JPEG, PNG, or WebP metadata up to 10 MB. Reading storage is
separate from the W2-04 water billing calculation.

Water charges are calculated through:

- `POST /api/v1/units/{unit_id}/water-bill/calculate`

The request identifies a meter and reading period. The endpoint requires an
effective metered water configuration and a matching reading, calculates
`usage = current_value - previous_value`, then charges the greater of
`usage * rate_per_unit` and `minimum_charge`. Calculations are branch-scoped
and audited; they do not issue or mutate an invoice.

Arrears ageing is available through `GET /api/v1/arrears?as_of=YYYY-MM-DD`.
Invoices are grouped into current, 1-30, 31-60, 61-90, and 90+ day buckets.
Branch policies configure grace days and percentage penalties. Managers can
approve a reasoned partial waiver with
`POST /api/v1/invoices/{invoice_id}/waiver`; the approval is audited.
`POST /api/v1/invoices/{invoice_id}/demand-letter` issues one dated, idempotent
demand letter with a 14-day response date. These operations are branch-scoped.

Arrears repayment plans are managed through:

- `GET /api/v1/payment-plans`
- `POST /api/v1/tenancies/{tenancy_id}/payment-plans/create`
- `POST /api/v1/payment-plan-instalments/{instalment_id}/pay`

Plans store the agreed total, instalment amount, reason, start date, status,
and generated due-date schedule. Each instalment tracks its due amount, paid
amount, and due, paid, or missed status. A plan becomes completed only after
every instalment is paid; only managers can create plans or record payments,
and all changes are audited.

M-Pesa C2B callbacks are received at
`POST /api/v1/payments/mpesa/callback`. The raw JSON payload is persisted
before processing. Transaction codes are unique, so repeated deliveries return
the original callback record without creating duplicates. Valid callbacks are
marked `processed`; malformed callbacks are retained as `failed` records with
their failure reason for safe retry investigation. Payment allocation is
handled by the payment allocation endpoint below.

Unmatched payments are retained for staff review through:

- `GET /api/v1/payments/unmatched`
- `POST /api/v1/payments/{payment_id}/allocate`

Reference-based tenant suggestions are advisory only. A Branch Manager,
Accountant, or Super Admin must choose the invoice and allocation amount;
manual allocation is audited and performed transactionally.

Matched payments receive an immutable receipt with the payment amount and
post-payment tenant balance. Receipt data is available as a download at
`GET /api/v1/payments/{payment_id}/receipt`.

Bank statements can be imported as CSV or XLSX files through
`POST /api/v1/bank-statements/import`. Rows must include `reference` and
`amount`; invalid rows are retained with validation errors, while valid rows
create unmatched bank payments for review. Staff can inspect rows through
`GET /api/v1/bank-statements/{statement_id}`, manually match a row through
`POST /api/v1/bank-statement-rows/{row_id}/match`, and view totals through
`GET /api/v1/bank-statements/{statement_id}/reconciliation`.

Cash and cheque payments are recorded only by finance-authorized staff through
`POST /api/v1/payments/offline/create`. Cash records the authorized collector
and is included in `GET /api/v1/payments/cash-report`. Cheques retain their
number and bank, start as pending clearance, and can be marked cleared or
bounced through `POST /api/v1/payments/{payment_id}/cheque-status`. Both
paths create immutable receipts and audit events.

Daily reconciliation is available to authorized property-management users
through `GET /api/v1/payments/daily-reconciliation?from=YYYY-MM-DD&to=YYYY-MM-DD`.
The report includes received, matched, unmatched, reversed, and allocated
amounts overall and per payment channel. Matched totals are invoice
allocations, allocated totals are payments fully allocated to invoices,
unmatched totals are remaining unallocated/uncredited amounts, and bounced
cheques are reported as reversed.

Refundable deposits are maintained separately from rent invoices and income
payments. The ledger is available through
`GET /api/v1/tenancies/{tenancy_id}/deposit`; finance-authorized staff record
receipts, top-ups, refunds, or adjustments through
`POST /api/v1/tenancies/{tenancy_id}/deposit/entries`. Each tenancy has a
configured target amount, current refundable balance, and calculated
`top_up_amount`. Deposit entries are immutable, references are unique, refunds
cannot exceed the current balance, and every movement is audited.

Tenant records are managed through:

- `GET /api/v1/tenants`
- `GET /api/v1/tenants/{tenant_id}`
- `POST /api/v1/tenants/create`
- `POST /api/v1/tenants/{tenant_id}/documents`

Tenant records support individual and company tenants. Individuals require an
ID or passport number; companies require a registration number and contact
person. KRA PINs and identity numbers are retained for authorized operational
use and are redacted in audit records. Blacklist entries require a reason.
Tenant records and KYC metadata are branch-scoped for staff access. Document
endpoints store metadata and an object-storage key only; binary uploads and
signed download URLs remain part of the later storage integration.

Unit reservations are managed through:

- `GET /api/v1/unit-reservations`
- `POST /api/v1/units/{unit_id}/reservations/create`
- `POST /api/v1/unit-reservations/{reservation_id}/release`

A reservation belongs to a vacant unit and an accessible tenant/applicant. Only
one active reservation can exist for a unit at a time. Creating a reservation
temporarily disables the unit's listing flag while preserving its previous
value. Releasing a reservation restores that value. Reservations whose expiry
time has passed are reconciled as `expired` when reservation records are read or
when a new reservation is attempted, and the unit becomes listable again. All
creation, release, and expiry events are audited.

Tenancies are managed through:

- `GET /api/v1/tenancies`
- `GET /api/v1/tenancies/{tenancy_id}`
- `POST /api/v1/tenancies/create`

A tenancy links a branch-scoped tenant to a unit and records whether it is
fixed-term or month-to-month, its dates, bill due day, grace period, notice
period, and configurable rent-escalation terms. Fixed-term tenancies require an
end date, dates must be valid, and due days must fall between 1 and 31. Active
tenancies cannot overlap for the same unit. Creating a current tenancy marks
the unit occupied; future tenancies keep it vacant but remove it from listings.
An active reservation for the same unit is released when the tenancy is created.

Move-in packages are generated through:

- `POST /api/v1/tenancies/{tenancy_id}/move-in-package`
- `GET /api/v1/tenancies/{tenancy_id}/move-in-agreement.pdf`
- `POST /api/v1/tenancies/{tenancy_id}/move-in-invoice`

Package generation is idempotent. The opening move-in invoice includes rent
prorated by actual calendar days from the move-in date through month-end,
security and configured utility deposits, and no recurring charges or letting
fees. Recurring charges and metered usage are billed separately; opening meter
readings are recorded during handover. The package also creates a five-item
move-in inspection and handover checklist. Invoice issuance requires a package
and is idempotent for the tenancy's move-in period.

Property-officer onboarding uses:

- `GET /api/v1/property-officer/move-in-quote`
- `POST /api/v1/property-officer/tenant-onboarding`

The quote includes only prorated first-month rent, the security deposit, and
configured utility deposits. Rent is prorated from the move-in day through
month-end, inclusive, using actual calendar days. Onboarding requires
confirmation of the full quoted payment and records its channel, reference,
and date, plus a tenant national ID/passport number and document (for a
company, the authorized contact person's). The upload is validated and stored
before activation; submit the onboarding request as `multipart/form-data`.
Onboarding creates the provisional tenant and tenancy, opening package and
invoice, allocates the confirmed payment through the standard payment ledger,
and issues a receipt. Recurring charges and metered usage are billed
separately. Landlord-created move-in reservations use the same upfront charge
composition; their recurring-charge snapshots are retained for subsequent
billing. The identity document, generated agreement, and receipt PDFs are
stored as private tenant documents and are served through the existing
authorized tenant-document endpoint.

Activation sends the verification code by SMS and requires phone verification
before the tenant account is activated. Move-in notifications currently use
email and SMS; WhatsApp notifications are deferred.

Tenant activation is managed through:

- `POST /api/v1/tenancies/{tenancy_id}/tenant-activation`
- `POST /api/v1/tenant-activation/complete`

Staff issue a single-use activation token for an active tenancy. The token is
stored only as a SHA-256 hash, expires after 24 hours, and is consumed when the
tenant sets a password. Activation creates or updates the tenant portal user
and assigns the `Tenant (portal)` role. The raw token is returned once and is
not written to audit logs.

Tenancy invoices are issued through:

- `POST /api/v1/tenancies/{tenancy_id}/invoices/create`

The endpoint creates an immutable invoice for a requested billing period from
the tenancy's effective rent and recurring charges. Invoice numbers are
sequential within each branch and year, and repeating the same tenancy period
returns the existing invoice rather than issuing a duplicate. Corrections use
the later credit-note workflow; issued invoices cannot be edited or deleted.

RabbitMQ-backed Celery Beat runs the billing cycle every minute in
`Africa/Nairobi`; Celery workers draft and publish invoices idempotently.
At 09:00 on the 30th, or February 27 in a common year / February 28 in a leap
year, it notifies caretakers assigned to properties with metered water.
Caretakers save readings as drafts,
and the server blocks publication until every active tenancy at that property
has a valid reading. Publishing creates the next month's invoice drafts and
queues an SMS for the Property Officer. Fixed-utility properties without
metered water are drafted on the normal billing day: the 30th, or the final
calendar day of shorter months.

Draft invoices carry the prior issued balance and itemized current rent,
recurring charges, and metered water. The scheduler issues eligible drafts on
the first day of the month even when a waiver request is still awaiting the
Director. The Director is the sole approver; an approved waiver is accounted
against the invoice and its amount and reason are shown to the tenant, including
when approval occurs after issuance. SMS notifications use the configured SMS
backend and audited delivery queue. WhatsApp delivery is not enabled until a
provider is configured.

The cycle can also be run manually with `python manage.py run_billing_cycle`.

The legacy one-shot command remains available for operations that explicitly
need to issue a requested period immediately:

`python manage.py generate_monthly_invoices --period YYYY-MM-01`

It remains idempotent, includes effective metered water when a reading exists,
and fails explicitly when required charges or readings are missing.

Property creation and changes are audited. Sensitive landlord payout values remain
redacted by the existing landlord audit policy.

## Move-out and deposit settlement

Property staff manage tenant move-outs through the audited Django workflow at
`/landlord/move-outs`. Tenant-submitted and staff-recorded notices are visible
through `GET /api/v1/move-out-workflows`; authorized staff can create, accept,
reject, or withdraw notices through the corresponding `/move-out-notices`
endpoints. Recording a notice immediately records the proposed tenancy end date
and marks the unit **Notice Given**; rejection or withdrawal restores the prior
end date and occupancy status. Notice submission, staff recording, and review
queue scoped SMS notifications through the configured Softlink outbox.

Accepting a notice requires the Property Officer to select an active caretaker
assigned to that property. Acceptance creates a pending `move_out` inspection
for 9:00 a.m. Africa/Nairobi time on the proposed move-out date, assigns it to
that caretaker, and sends the caretaker and tenant SMS notifications.
Withdrawing the notice cancels the scheduled task; withdrawal is blocked once
the task has started.
The caretaker sees the pending task in the dashboard or in the verified
WhatsApp bot under **Checklists & inspections → Move-out inspections**. Either
path records the actual move-out date, every configured final meter reading,
itemized damage, findings, recommendations, maintenance needs, and optional
evidence photos. The dashboard uses
`POST /api/v1/inspections/{inspection_id}/complete` and
`POST /api/v1/inspections/{inspection_id}/photos`; WhatsApp submits through the
same completion workflow. Completion notifies the tenant and
property-management staff by SMS.

For an accepted move-out, the ordinary monthly invoice retains the regular
metered-water charge based on the published monthly reading, but excludes rent
and fixed monthly utilities for the month the tenant is leaving. The final
reading measures any additional usage since that regular reading through the
actual move-out date; its charge is deducted from the deposit, not issued as a
second invoice.

Property staff prepare the financial settlement with
`POST /api/v1/move-out-settlements/create`. The service derives the deposit
balance and issued-invoice arrears, calculates the final metered-utility charge
from the caretaker's final readings, and applies that charge and itemized
damage estimates to the deposit settlement. Manual prorated rent credits and
overstay penalties are not part of this workflow.
The settlement records any amount not covered by the deposit as a tenant debt
shortfall; issued invoices remain immutable. The unit moves to **Deposit
Pending**, but the tenancy is not closed yet.

A different Branch Manager must approve or reject the settlement.
An Accountant with finance MFA records the manual payout reference and date
through `POST /api/v1/move-out-settlements/{settlement_id}/payout`; this records
the deposit-ledger movements, closes the tenancy, and marks the unit **Vacant**
or **Maintenance** according to the inspection decision. Payout execution
remains manual; this endpoint does not initiate an M-Pesa B2C transfer.
Changes to invoice balances between preparation, approval, and payout require
settlement recalculation. The tenant can download the scoped final statement
from `GET /api/v1/tenant/move-out-settlements/{settlement_id}/statement`.
Critical notice, inspection, settlement, approval, and payout steps queue SMS;
the payout SMS includes the statement link.
Changes, readings, and financial movements are audit logged. Rejected
settlements can be corrected and resubmitted.

## Payment allocation

Authenticated finance and property-management users can record a tenant
payment with `POST /api/v1/payments/create`. The service locks the tenant's
issued invoices and allocates the payment by due date, then period and ID.
Any amount remaining after all outstanding invoices are paid is recorded as
tenant credit. Payment references are unique, allocation totals are
transactional, and the original payment is retained for auditability.
