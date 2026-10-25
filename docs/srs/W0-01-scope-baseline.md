# W0-01 — SRS scope and delivery baseline

**Status:** Approved for planning by project owner on 24 October 2026
**Backlog item:** [W0-01 — Baseline SRS scope and decisions](https://github.com/davidnjau/Legacy-Real-Estate/issues/6)
**Source:** [Legacy Real Estate Agency — System Requirements Specification](https://docs.google.com/document/d/1AbwUbZ9NO1AL77SWdsERqQkgy_xw2Z57k8unsJp--bI/edit), dated 17 October 2026

## Purpose

This document classifies the requirements in the SRS for delivery planning.
The phase assignment below follows SRS section 2.6. A module can be part of
the overall product scope while being delivered after V1. An explicit
“later” note inside a module takes precedence for that feature.

The SRS does not assign every detailed requirement to an individual phase.
Where the phase summary does not decide timing, the requirement is marked
**Needs phase decision** rather than silently included in V1 or deferred.
The detailed SRS remains the source of truth; the section references below
identify groups of related requirements, not new SRS requirement IDs.

## Delivery classification

### V1 — Phase 1: Core rent collection

SRS section 2.6 explicitly names properties, units, tenants, billing,
M-Pesa C2B, landlord payouts, and basic reports as Phase 1. The following
section register maps the named SRS areas to delivery. “V1” means the
capability is explicitly part of Phase 1 or is a stated prerequisite for
those Phase 1 workflows; “Needs phase decision” means the SRS does not
provide enough detail to assert a phase.

| SRS reference | Classification | Requirement group |
|---|---|---|
| Sections 1.1–1.3 | V1 context | Rental operations in Nairobi and Nakuru, branch-scoped records, agency-managed properties, and core terminology inform Phase 1. Land sales is overall product scope but Phase 2 below. |
| Section 2 | V1 foundation; role list needs phasing | Configurable role/branch access and financial/audit rules apply to V1. The back-office, caretaker, landlord, and tenant access needed for Phase 1 rental work is V1. Confirm the activation timing and permission details for Accountant and other roles. |
| Section 3 | V1 | Property record (location, branch, type, landlord, caretaker, amenities, files and rules); unit record (code, type, size/features, rent/deposit/charges, meters and status); bulk creation, rent review/notices, vacancy board, unit history and effective dating. Public listing publication is Phase 3; the listing flag is retained in V1 property/unit data. |
| Section 4 | V1 | Landlord contact/identity/payout/documents; management agreement dates, commission, letting fee, deposit holding, expenses/approval thresholds, payout schedule and tax flag; landlord statements, approval requests and portal dashboard needed for V1 payouts/oversight. |
| Section 5 | V1 | Tenant records/documents; reservation and expiry, agreement generation, move-in invoice/checklist and activation; tenancy terms, renewals/rent increases/transfers; move-out notice/inspection/final readings/refund approval; blacklist and tenant statement. |
| Section 6 | V1 | Monthly and move-in invoices, charge lines, metered water/manual readings, one-off charges, credit notes and sequential numbering; payment allocation, raw/idempotent M-Pesa callbacks, unmatched queue, overpayment credit, receipts/reconciliation, arrears, penalties/waivers, demand letters/payment plans and deposit ledger/top-up. Confirm which non-M-Pesa channels are V1. |
| Section 7 | Later — Phase 2 | Land projects/plots, sales pipeline, buyers, payment plans, due diligence, transfer tracking, and land-sales commissions. Interactive site map is a later enhancement. |
| Section 8 | Needs phase decision | Ticket creation/status, approvals, vendors, caretaker mobile view, meter photos, move-in/out checklists, inspections and recurring expenses are described, but not phased. Decide minimum caretaker/readings/checklist support needed for Phase 1 invoices and tenancy. |
| Section 9 | Mixed: V1 transactional SMS receipts; WhatsApp later — Phase 2; other messaging needs decision | SMS receipts are supported by Phase 1 acceptance examples. WhatsApp is Phase 2. Decide timing for reminders, email, templates, delivery reports/credits, bulk messages, marketing opt-out, automatic triggers and tenant notice board. |
| Section 10 | Mixed: V1 remittance capability; advanced accounting later — Phase 2; full GL replacement out of V1 | Section 10.2 remittance run (calculation, manager approval, bank/M-Pesa payout, statements) is V1 by section 2.6, but execution level needs confirmation. Section 10.1 double-entry journal, chart of accounts, reconciliation and external-accountant exports need a V1/Phase 2 decision. MRI/VAT reports and tax-agent behavior need a boundary decision; eTIMS is Phase 2; full KRA integration is Phase 4. |
| Section 11 | Later — Phase 3 | Public website pages/listings, filters/SEO, automatic listing availability, lead sources/status, agent/caretaker viewings, matching and conversion report. V1 stores the publication flag and listing data on properties/units but does not publish them publicly. |
| Section 12 | Mixed: basic reports V1; advanced dashboards and compliance reports later — Phase 3 | Phase 1 basic report set to be confirmed. Section 12 report inventory includes rent roll, collections, arrears, vacancies, tenancy expiry/rent review, deposits, landlord statements/remittances, water use, plots, land collections/transfers, leads and audit log. Land reports follow Phase 2; advanced dashboard/compliance reports are Phase 3. |
| Section 13 | V1 legal/security controls; land-specific controls with Phase 2 land sales; compliance reporting Phase 3 | V1 rental/data-protection/agency registration/tax-notice/record retention controls apply before go-live. AML/KYC and land due-diligence controls apply to the land-sales phase; confirm exact agency legal obligations. Compliance reports are Phase 3, not the underlying legal safeguards. |
| Section 14 | Cross-cutting, required at the applicable release gate | Security requirements (HTTPS, password protection, finance 2FA, server-side access control, encryption/masking, callback protection, session controls, immutable audit and security review); scale/page/invoice/payment targets; availability/backups/restore; mobile-first KES/Nairobi locale, phone validation and migration reconciliation. Production hardening is Phase 4, but go-live criteria must be met before the relevant release. |
| Section 15 | Phased by delivered capability | The data model is guidance for the corresponding phased modules, not a requirement to implement every entity in V1. Preserve money, branch, tenancy, and audit relationships needed by V1. |
| Section 16 | Stack/integration decisions | Use the team-agreed Django, PostgreSQL, and Next.js stack instead of choosing among SRS alternatives. M-Pesa C2B is V1; WhatsApp/eTIMS are Phase 2; bank statement import/API, SMS/email providers, Maps, and other integrations need the timing/ownership decisions below. |
| Section 17 | Local development is W0 foundation; staging/production infrastructure is phased | Local/dev/staging/production environments, fake/anonymised test data, queued partner calls, backups, and CI are requirements. The local stack and safe examples are W0; single-node dev/staging and production Kubernetes/failover details must be delivered at their respective infrastructure tasks and release gates. Production Kubernetes is Phase 4. |
| Section 18 | Applies to each phase | Staging deployment, demo, agency UAT, project-manager sign-off, tests, documentation, user guide, and change log are required at each phase gate. Section 18.2 supplies cross-phase acceptance examples. |

V1 acceptance examples from section 18.2 include payment matching and
receipts, unmatched payment allocation, duplicate callback protection,
monthly invoices, penalty and waiver handling, landlord statement totals,
deposit refund calculation and approval, branch/tenant data isolation, and
resilient callback processing. Confirm the precise test set and rollout
conditions before Phase 1 sign-off.

### Explicit V1 exclusions and explicitly deferred features

| SRS reference | Classification | Scope boundary |
|---|---|---|
| Section 1.2 | Out of V1 | Full general-ledger accounting that replaces the external accountant. This does not remove V1 requirements to track rent, deposits, expenses, collections, and landlord payouts accurately. |
| Section 1.2 | Out of V1 | Payroll. |
| Section 1.2 | Out of V1 | Smart-meter hardware integration. Manual meter readings and water billing are described in section 6.1. |
| Sections 1.2 and 5.2 | Later enhancement | Native mobile app and e-signatures; the SRS specifies mobile-friendly web and PDF agreements for the initial workflow. |
| Sections 7.1, 16 | Later enhancement | Interactive land site map and bank API integration, respectively. |
| Sections 6.4 and 13 | Outside the system | Executing legal distress action through an auctioneer and providing legal advice. The platform may retain records/notices to support agency processes. |

The SRS does not identify any additional requirement as permanently out of
scope. Items explicitly deferred to a later enhancement are not classified as
permanently out of scope.

## Requirements needing a phase or scope decision

The following timing or interpretation is not resolved by the SRS phase
summary. Confirm these before treating the associated behavior as a V1
commitment:

1. **Maintenance and caretaker operations — section 8:** maintenance tickets,
   quotations/approvals, vendor register, inspections, recurring expenses,
   and caretaker actions are part of the overall scope, but section 2.6 does
   not assign them to a phase. Meter readings and move-in/out checklists are
   referenced by Phase 1 billing and tenancy workflows; confirm the minimum
   caretaker functionality needed for Phase 1.
2. **Communications — section 9:** SMS is the primary channel and V1
   acceptance examples require SMS receipts. WhatsApp is Phase 2. Confirm
   whether transactional email, message templates, bulk messaging,
   marketing opt-out, and tenant notice boards are V1 or later.
3. **Accounting and tax — section 10:** section 1.2 excludes a full
   general-ledger replacement from V1; section 2.6 puts advanced accounting
   in Phase 2 and full KRA integration in Phase 4. Confirm the required V1
   ledgers, bank/M-Pesa reconciliation, landlord statements, MRI calculations
   or exports, VAT handling, and agency expense tracking.
4. **Landlord payouts — sections 2.6 and 10.2:** payouts are explicitly V1,
   but confirm whether V1 requires actual M-Pesa B2C/bank disbursement or
   only approved remittance calculations, statements, and recorded payments.
5. **Public listings — sections 1.2, 2.6, 3.2 and 11:** public listings are
   broadly included in the overall scope, but the website is assigned to
   Phase 3. Confirm that automatic publishing is deferred while the V1
   listing flag and property/unit data remain supported.
6. **Compliance reporting — sections 2.6 and 13:** confirm which legal
   controls and data-handling requirements are release blockers for V1 and
   which reports are Phase 3. Legal compliance itself should not be treated
   as optional merely because compliance reporting is later.
7. **Roles and permissions — section 2:** confirm which of the nine roles
   are needed in Phase 1. Server-side role/branch isolation, own-record
   access for landlord/tenant portals, and restricted caretaker access are
   foundational security requirements.
8. **Phase 1 bank channels — section 6.2:** the SRS lists bank deposit,
   bank Paybill, cheque, and cash in addition to M-Pesa, but Phase 1 names
   M-Pesa C2B. Confirm which non-M-Pesa channels are required in V1.

## Business and accounting decisions to record

These are policy choices or configurable values identified in the SRS and
approved as unresolved items for planning. Resolve each with the accountable
business/legal/finance owner before implementing the related behavior; do not
substitute developer assumptions.

| Decision | SRS reference | Decision needed |
|---|---|---|
| Operational accounting boundary | Sections 1.2, 10.1–10.4 | Define V1 books and reports versus the external accountant's full GL. Confirm whether V1 needs the three separate pots (agency income, landlord trust, deposits trust), a double-entry journal, or an interim operational ledger. |
| Deposits | Sections 4.2, 5.4, 6.5, 10.1 | Decide who holds tenant deposits, the required segregation/reconciliation, and approval/evidence for deductions and refunds. The formula is deposit held minus arrears, damages, and unpaid utilities; confirm its application and dispute process. |
| Landlord commission, expenses, tax and payouts | Sections 4.2, 8.4, 10.2–10.4 | Approve agreement terms, expense approval thresholds, payout schedule, deduction order, payout approval, and which deductions/tax apply. Confirm whether V1 performs payouts or records them. |
| Tax rules and agency appointment | Sections 4.2, 10.3, 13 | Have the agency accountant/lawyer validate MRI rates/thresholds, VAT applicability, tax-agent status, filing responsibility, and required V1 exports. Keep rates configurable as specified. |
| Charges, rent changes and arrears | Sections 3.3, 5.3, 6.1, 6.4 | Set invoice day/due day, proration, grace periods, penalty rules, allocation order, rent review notice periods, waiver permissions, and payment-plan approvals. |
| Payment channels and reconciliation | Sections 6.2–6.3, 17.4 | Confirm V1 channels, Paybill configuration, cash permissions, statement/reconciliation process, unmatched payment authority, and treatment of fees, duplicates, reversals, and overpayments. |
| Maker-checker roles | Sections 2, 4.2, 5.4, 6.4, 8.1, 10.2 | Name who may create and approve rent changes, waivers, refunds, expenses, maintenance work, and payouts; ensure requester and approver are distinct where required. |
| Data protection and retention | Sections 13–14.1 | Confirm controller/processor responsibilities, ODPC registration, consent/privacy text, retention schedules, data-subject request handling, and access to sensitive ID/KRA/bank data with agency counsel. |
| AML/KYC and cash thresholds | Section 13 | Agency counsel/compliance owner to confirm required buyer/seller due diligence, source-of-funds handling, cash thresholds, and record-retention responsibilities. |
| Rental and legal templates | Sections 5, 6.4, 13, 18.3 | Have agency counsel approve tenancy, notice, demand-letter, land, and related templates and configurable notice periods before production use. |
| Legacy opening balances | Section 14.5 | Identify the source records, reconciliation owner, and sign-off process for properties, tenants, opening balances, and deposits before go-live. |

## Cross-cutting delivery requirements

These requirements apply to the delivery as a whole; their implementation
may be phased, but they must be planned and explicitly gated:

- **Access and audit (sections 2, 13–14):** configurable role and
  branch-scoped permissions enforced server-side; immutable audit trail;
  financial records reversed rather than hard-deleted; sensitive actions
  use maker-checker approval.
- **Security and privacy (sections 13–14.1):** HTTPS, secure password
  hashing, staff 2FA for finance rights, encryption/masking of sensitive
  data, protected/idempotent payment callbacks, session protections, and
  security review before go-live.
- **Money correctness (sections 6, 10, 18):** transactional and
  idempotent payment handling; tested invoice, allocation, penalty,
  commission, MRI, and deposit-refund calculations.
- **Reliability (sections 14.3, 17–18):** backups and restore tests,
  queued/retried partner integrations, callback durability, and explicit
  failover/pod-recovery acceptance at the environment where those
  infrastructure features are deployed.
- **Usability and data quality (sections 14.2, 14.4–14.5):** mobile-first
  caretaker/tenant flows, KES and Nairobi time/date conventions, Kenyan
  phone validation, import validation, and reconciled opening balances.
- **Delivery gates (section 18):** staging deployment, agency demo and UAT,
  project-manager sign-off, updated schema/API docs, money-calculation test
  results, user guide or role video, and a delivered-vs-SRS change log.

## Source conflicts and interpretation

1. Section 1.2 describes a broad overall product scope, including public
   listings, integrations, communications, accounting, and reports. Section
   2.6 schedules some of those capabilities for later phases. This baseline
   treats section 2.6 as the delivery schedule and section 1.2 as overall
   product scope.
2. Section 1.2 excludes full GL replacement, payroll, and smart-meter
   hardware from V1, while section 10 describes a double-entry journal.
   Confirm whether the journal is a later-phase advanced accounting feature
   or a V1 implementation detail for operational financial screens.
3. Section 1.2 lists bank integrations and WhatsApp in the overall scope,
   while section 2.6 assigns WhatsApp to Phase 2 and does not assign the bank
   channels in section 6.2 to a phase. Phase 1 explicitly names M-Pesa C2B.
   Confirm whether bank collection/payout channels are V1.
4. The system architecture/stack in sections 16–17 allows alternatives.
   This project has separately agreed Django, PostgreSQL, and Next.js.
   Treat that team decision as the implementation choice, while preserving
   the SRS's functional and operational requirements.

## W0-01 completion checklist

- [x] Locate and review the complete SRS (17 October 2026).
- [x] Classify the explicit phase-level scope and explicit V1 exclusions.
- [x] Identify SRS areas whose phase is not specified and record them for
  decision instead of guessing.
- [x] Record unresolved business/accounting boundaries.
- [x] Obtain project-owner approval for this baseline and its recorded
  unresolved items.

W0-01 is complete. Unresolved policy decisions remain explicit implementation
gates for the features they govern; approval of this planning baseline does
not replace legal, accounting, or operational approval of those detailed
rules.
