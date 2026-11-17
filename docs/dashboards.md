# Dashboards

Role-specific dashboards for the Legacy Real Estate Agency platform.

The Director and Property Officer experiences share the Agency portal but use
separate role-protected views and sidebars. The Director portal has separate
overview (`/admin/dashboard`), staff (`/admin/staff`), audit (`/admin/audit`),
and settings (`/admin/settings`) pages. The Property Officer portal has
overview (`/property-officer/dashboard`), properties
(`/property-officer/properties`), units (`/property-officer/units`), tenants
(`/property-officer/tenants`), leases (`/property-officer/leases`), viewings
(`/property-officer/viewings`), and maintenance
(`/property-officer/maintenance`) pages.

Both portals require a real Django session and the corresponding active agency
membership; prototype demo-role cookies do not grant access. Their agency and
branch selectors are populated from the signed-in user's memberships, so
Directors see branches in their active agency and Property Officers see only
branches assigned to their membership. Overview summary cards still use prototype fixtures and are read-only.
Operational Property Officer maintenance tickets, conversations, assignment,
repair completion, and the maintenance ledger use the role-authorized Django
workflows and PostgreSQL records.
Property Officer leads and viewing appointments are also persisted, audited,
and filtered to branches granted by the signed-in user's active agency
membership; the lead manager creates records and updates lead/viewing status.
The Property Officer legacy-import workspace supports branch-scoped CSV and
XLSX preview/commit imports for properties, units/tenants, charges, opening
arrears, payments, meter readings, and deposits. Preview writes are rolled
back; commits are atomic, reject invalid batches, and skip duplicate records.
Caretakers cannot access the import workspace. The Migration ledger
(`/property-officer/ledger`) displays imported opening arrears, confirmed
payments allocated to those balances, approved waivers, and the remaining
outstanding amount, with branch, property, and payment-status filters.
The Monthly property ledger (`/property-officer/monthly-ledger`) provides
property and historical-month views of itemized rent, recurring charges, and
utilities, prior outstanding balances, confirmed payments allocated during the month,
waivers, remaining balances, and cleared/partial/unpaid filters. Historical
month balances are reported as of that month's end; the current month updates
through today. Repeated carried-balance invoice lines are not counted as new
charges when calculating prior arrears.
The reminder planner records an occurrence three days before an invoice is due,
on its due date, and every three days after that while the invoice remains
outstanding. Message stages use the arrears ageing bands so the pre-due, due-day,
and increasingly overdue notices can use different wording. Schedule
`python manage.py plan_arrears_reminders` as a daily job to plan occurrences for
that day. The planner audits SMS and WhatsApp records with a per-invoice,
channel, and date uniqueness guard; it does not dispatch messages. SMS delivery
remains gated on Softlink configuration, and WhatsApp remains disabled until its
provider integration is supplied.

The local Landlord portal uses a portfolio-focused sidebar. Its primary
sections are overview (`/landlord/dashboard`), properties
(`/landlord/properties`), occupancy (`/landlord/units`), rent and arrears
(`/landlord/ledger`), expenses (`/landlord/expenses`), statements
(`/landlord/statements`), and remittances (`/landlord/remittances`). Expense
totals are read from the landlord dashboard API and are scoped to properties
owned by the authenticated landlord account.

| Role            | Dashboard                                                                                      |
| ---------------- | ----------------------------------------------------------------------------------------------- |
| Director         | Local Agency portal at `/admin/dashboard`                                                       |
| Property Officer | Local Agency portal at `/property-officer/dashboard`                                            |
| Branch Manager    | [Branch Manager Dashboard](https://branch-manager-legacy-real-estate.lovable.app)               |
| Landlord          | [Landlord Dashboard](https://real-estate-agency-legacy-real-estate.lovable.app)                 |
| Tenant            | [Tenant Dashboard](https://tenant-legacy-real-estate.lovable.app)                               |

> The external Lovable-hosted dashboards are prototypes, not part of this
> repo's build/deploy pipeline. Treat them as reference/UX material until their
> functionality is absorbed into `apps/website` or `apps/core`.
