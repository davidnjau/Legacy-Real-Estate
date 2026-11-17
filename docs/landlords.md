# Landlord records and management agreements

W1-02 stores a canonical landlord record with a home branch and dated
management agreements under each landlord. A landlord can own properties in
multiple branches while using one portal account.

## Access

The landlord API is available only to authenticated users with one of these
staff roles:

- Super Admin (Director)
- Branch Manager
- Accountant
- Property Officer / Agent

Results include landlords whose home branch or linked properties are in a
branch assigned to the signed-in user. Super Admin (Director) can access all
branches through the existing access model. Requests for an inaccessible
landlord return `404` to avoid exposing whether another branch has a matching
record.

## Data boundary

Landlord records store identity/contact data and a payout-details object.
Management agreements store effective dates, commission terms, payout day,
MRI flag, and additional agreed terms. Staff approve remittances and record
manual payout evidence in the system; the API does not transfer money.

Landlord and agreement creates and administrative changes are written to the
append-only audit log. The API currently exposes read and create operations;
updates and deletion remain controlled through the audited Django admin until
their workflow-specific authorization rules are defined.

## Property Officer onboarding

The Property Officer landlord onboarding form lists properties in the selected
branch. A newly created landlord can optionally be assigned one of that
branch's unassigned properties. Properties already assigned to another landlord
remain visible but cannot be selected. Property registration also supports
selecting a landlord already created for the same branch.

## Landlord statements

The landlord portal can retrieve a date-range statement through
`GET /api/v1/landlord/statements?from=YYYY-MM-DD&to=YYYY-MM-DD`; add
`format=csv` to download the same audited statement as CSV. Access is limited
to the authenticated landlord's linked properties. The endpoint does not
create a remittance, approve a payout, or transfer funds.

Statement and remittance figures use the same calculation rules:

- Billed rent is the sum of rent invoice lines for issued invoices whose
  billing-period start is within the requested date range.
- Collected invoice allocations are valid allocations to issued invoices where
  the payment was received in the requested date range. Pending-clearance and
  bounced cheques are excluded. Since allocations are invoice-level, this
  amount can include water and other charges; it is shown separately from
  billed rent and is the configured percentage-commission basis.
- Arrears are calculated as of the period end, after valid payments and
  approved waivers, including applicable overdue penalties.
- Only approved property expenses with an effective date in the requested
  period are deducted. The current expense model does not record disbursement,
  so this is not a cash-paid expense report.
- Approved maintenance quotations can have cost allocations assigned to the
  landlord or tenant. Only landlord allocations from approved quotations are
  deducted from the landlord statement, based on the allocation date. They are
  listed separately from property expenses and included in remittances prepared
  after the allocation is recorded.
- Percentage commission is calculated from collected invoice allocations for
  the dates covered by each effective management agreement. A fixed fee is
  charged once for each statement period covered by a fixed-fee agreement.
- MRI/tax is not inferred from the agreement flag alone. For an agreement with
  `mri_enabled=true`, tax is calculated only when `terms.landlord_statement_tax`
  contains an approved configuration, for example:

  ```json
  {
    "name": "MRI",
    "rate": "5.00",
    "basis": "collected_invoice_allocations",
    "approved": true
  }
  ```

  The rate is a percentage from 0 through 100. If the agreement enables MRI
  but this approved configuration is missing or invalid, tax and net payout
  are returned as `null`; the portal prompts the landlord to have the
  configuration reviewed. The statement's net payout is an estimate of
  collections less commission, approved in-period expenses, and configured
  tax; it is not a recorded or approved remittance.

Statement views and CSV exports are recorded in the immutable audit log with
the landlord and requested period.

## Maintenance quotation approvals

Landlords can review submitted maintenance quotations for their own properties
when the quotation amount exceeds the active management agreement's
`maintenance_approval_threshold` (or its legacy `approval_threshold` term).
The `GET /api/v1/landlord/maintenance/quotations` queue is owner-scoped;
`POST /api/v1/landlord/maintenance/quotations/{id}/decision` accepts an
approval or a rejection with a required reason. Approval advances a ticket
awaiting quotation review to in progress. Decisions are recorded in the audit
log. Staff quotation and cost-allocation workflows remain separate.

## Landlord remittances

Authorized staff prepare one remittance per landlord and period at
`POST /api/v1/landlord/remittances/create`. The persisted snapshot freezes the
statement calculations, including landlord maintenance allocations recorded
before preparation, used for the run. Later allocations do not silently mutate
an existing remittance snapshot; a rejected run can be replaced by a
new revision for the same period; an active or completed run cannot be
duplicated. Preparation is blocked when commission or applicable approved tax
configuration is incomplete, or when deductions exceed collections.

Only a different Branch Manager or Super Admin may approve or reject a
prepared remittance. Rejections require a reason. Finance-authorized staff
record a positive payout using a reference and payment date after approval;
the reference is unique. Zero-value remittances close as `no_payout` when
approved. No funds are transferred by the application. Each preparation,
decision, and payout record is written to the immutable audit log.

`GET /api/v1/landlord/remittances` is branch-scoped for staff and returns only
the authenticated landlord's own remittances for a linked landlord portal.
