# Authentication and access foundation

This foundation implements the authentication decisions agreed for W0-05. It
does not add business workflows or permissions beyond the approved SRS.

## Sign-in

- Users sign in with either their email address or Kenyan mobile number; a
  phone-only user may leave email blank.
- Store mobile numbers in international format without a leading plus, using
  `2547XXXXXXXX` or `2541XXXXXXXX`.
- Browser clients use Django session cookies. Fetch a CSRF token from
  `GET /api/v1/auth/csrf` and send it as `X-CSRFToken` on unsafe requests.
- Finance-role users and Django superusers signing into Django admin must
  also complete SMS verification before an admin session is established.
- Finance-role users and superusers need a `UserProfile` with a valid phone
  number before signing in. Provision profiles for `createsuperuser` accounts
  through a trusted Django management process before their first login.
- `POST /api/v1/auth/login` accepts `identifier` and `password`. A successful
  non-finance login returns the authenticated user. Finance-role login returns
  `202` with an opaque challenge ID; the code is never returned in an API
  response.
- `POST /api/v1/auth/mfa/verify` accepts `challenge_id` and `code`.
- `GET /api/v1/auth/me` returns the current user's role names and accessible
  branches. `POST /api/v1/auth/logout` ends the session.
- The SMS provider is supplied by `AUTH_SMS_BACKEND`. The development-only
  console backend is selected when `DEBUG=1`; it refuses to send when debug
  mode is disabled. Production has no fallback provider and fails closed until
  one is configured.
- Successful and failed password/MFA sign-in attempts through the API or
  Django admin, as well as successful sign-outs, are written to the read-only
  `AuditLog` table with the actor when known and the request IP. Passwords
  and SMS codes are never included.

## Audit and record integrity

The append-only audit log records authentication events and user, role, and
branch administration changes with actor, action, entity, timestamp, old/new
values, and request IP. Application writes are centralized in `users.audit`;
model, queryset, admin, and PostgreSQL trigger protections reject audit-log
edits and deletes. When an audited user is deleted, the actor reference may be
cleared for referential integrity; the event details remain unchanged.

No rental or financial workflow records exist in this foundation. When those
models are implemented, financial records must be reversed rather than
hard-deleted, and their create/edit/approval events must use this audit
mechanism. This release adds no separate audit API endpoint; audit access is
read-only through Django admin.

Enforce this rule as each financial record type is introduced, with
record-specific correction workflows and tests rather than a generic reversal
model. The first implementation points are W2-01 invoices (immutable issued
records corrected by credit notes), W2-09 onward payment allocation and
receipts, W2-14 deposits, W4-05 deposit settlement, and W4-06 landlord
remittances. W6-01 explicitly requires balanced immutable journal entries and
reversal entries instead of destructive edits. A task that creates a
financial record must include tests proving that its applicable correction
workflow preserves the original history.

The SMS challenge lifetime and attempt limit are environment-configurable
(`AUTH_SMS_CODE_LIFETIME_SECONDS` and `AUTH_SMS_MAX_ATTEMPTS`). Their current
defaults are implementation settings, not values specified by the SRS.
Repeated password failures lock an account temporarily. The threshold and
lock duration are configurable using `AUTH_LOGIN_MAX_ATTEMPTS` and
`AUTH_LOGIN_LOCKOUT_SECONDS`; their defaults are also implementation settings,
not policy values specified by the SRS.

## Roles and branch membership

The nine groups below are seeded using the exact SRS role names. Users can
belong to multiple role groups and branches. The `Super Admin (Director)` role
can access all seeded branches; other users see only their assigned branches.

1. Super Admin (Director)
2. Branch Manager
3. Accountant
4. Property Officer / Agent
5. Land Sales Agent
6. Caretaker
7. Landlord (portal)
8. Tenant (portal)
9. Land Buyer (portal)

The initial branch records are Nairobi and Nakuru. Role groups use Django's
configurable permissions, and the Super Admin group is granted access and
management permissions for users, branches, and role permissions. The SRS
business-module permissions will be applied with their corresponding
workflows; this foundation does not grant undocumented access to those
modules.

Finance-role SMS verification applies to Super Admin (Director), Branch
Manager, and Accountant, as selected for W0-05. Django superusers also
require SMS verification because their privileges exceed those role groups.
SMS-provider integration remains deferred to W0-09.
