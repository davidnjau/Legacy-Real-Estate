# Authentication and access foundation

This foundation implements the authentication decisions agreed for W0-05. It
does not add business workflows or permissions beyond the approved SRS.

## Sign-in

- An agency Director is provisioned only by the trusted operator through the
  `bootstrap_director` Django management command. It creates or selects the
  agency, adds the Director role to that person's agency membership, and never
  grants Django superuser or staff privileges. A new login is inactive and
  password-disabled until it is activated using a single-use email OTP and a
  new password. An existing active login can be granted Director membership
  in another agency without another account or password reset. No public
  Director signup or self-approval flow is provided. Run the command from
  `apps/core` after migrations and email configuration are ready:

  ```sh
  python manage.py bootstrap_director \
    --agency-name "Legacy Real Estate" \
    --agency-slug legacy-real-estate \
    --name "Director Name" \
    --email director@example.com \
    --phone 254712345678 \
    --authorized-by "change or approval reference" \
    --confirm PROVISION-AGENCY-DIRECTOR
  ```

- Before running the command, the trusted operator must verify the agency and
  Director identity/email/phone out of band. The required `--authorized-by`
  value is recorded in the audit event. Activation email is sent only after
  the account transaction commits. The OTP is hashed at rest, expires after
  15 minutes by default, and is invalidated after five failed attempts or one
  successful use. If delivery fails, the account remains inactive and
  password-disabled; rerun the provisioning command to issue a fresh code.
  Existing Nairobi and Nakuru branch data are assigned to the initial
  `legacy-real-estate` agency by migration.
- A login may belong to multiple agencies. Each membership carries its own
  roles and branch assignments. Branch access is limited to the assigned
  branches in that membership's agency. The Director role grants access to all
  branches in that Director's agency only. Agency membership is the tenant
  boundary; global Django groups are retained for legacy compatibility and
  are not sufficient to grant Django admin access.
- The Director dashboard requires a real Django session and the
  `Super Admin (Director)` role; prototype demo-role cookies do not grant
  access. From the dashboard, Directors can manage agency staff only within
  their own active agency membership. The staff roles currently available
  there are Branch Manager, Accountant, Property Officer / Agent, and Land
  Sales Agent; Director, landlord, caretaker, and tenant accounts use separate
  provisioning or onboarding flows.
- `GET/POST /api/v1/director/staff` lists or creates staff for an `agency_id`.
  `PATCH/DELETE /api/v1/director/staff/<membership_id>` updates role, branch
  scope, or active status, or suspends that agency membership.
  `POST /api/v1/director/staff/<membership_id>/resend-activation` issues a new
  activation code for a pending account. Branch assignments are validated
  against the Director's agency. Suspending staff disables only that agency
  membership; it does not disable a login or its memberships in other agencies.
- A new staff login is inactive and password-disabled until the invitee submits
  the one-time email OTP and a new password to
  `POST /api/v1/auth/staff/activate`. Staff OTPs are hashed at rest, expire
  after 15 minutes by default, and are invalidated after five failed attempts
  or a successful activation. Existing active accounts can receive a new
  agency membership without resetting their password. Email delivery failures
  leave the account pending so the Director can retry with Resend activation.
- The Director, agency staff, tenant, and caretaker activation forms share the
  same website password checks: at least 12 characters with lowercase,
  uppercase, number, and symbol, plus matching confirmation. Django password
  validation is also applied by the activation endpoints.
- The Next.js website proxies browser authentication requests to these Django
  endpoints, so the browser receives the Django `sessionid` and `csrftoken`
  cookies without exposing backend credentials or maintaining a second portal
  session.
- Users sign in with either their email address or Kenyan mobile number; a
  phone-only user may leave email blank.
- Store mobile numbers in international format without a leading plus, using
  `2547XXXXXXXX` or `2541XXXXXXXX`.
- Browser clients use Django session cookies. Fetch a CSRF token from
  `GET /api/v1/auth/csrf` and send it as `X-CSRFToken` on unsafe requests.
- Authenticated sessions expire after eight hours by default. Configure the
  positive lifetime in seconds with `AUTH_SESSION_AGE_SECONDS`; expired sessions
  are rejected by `GET /api/v1/auth/me` and portal routes return users to sign-in.
- Director and finance-role website sign-ins require authenticator-app TOTP
  before a session is established. First sign-in after activation requires
  enrolling an authenticator app. The TOTP secret is encrypted at rest; ten
  one-time recovery codes are shown once after enrollment and stored hashed.
  The encryption key is derived from `DJANGO_SECRET_KEY`; rotating that key
  requires coordinated TOTP re-enrollment.
  MFA challenges expire after five minutes by default and are limited to five
  attempts. Finance-role users and Django superusers signing into Django admin
  continue to use its existing SMS verification flow.
- Finance-role users and superusers need a `UserProfile` with a valid phone
  number before signing in. Provision profiles for `createsuperuser` accounts
  through a trusted Django management process before their first login.
- `POST /api/v1/auth/director/activate` accepts `challenge_id`, `code`, and
  `password`, consumes a valid activation challenge, and activates the account.
- `POST /api/v1/auth/login` accepts `identifier` and `password`. A successful
  non-finance login returns the authenticated user. Director and finance-role
  login returns `202` with an opaque TOTP challenge ID. During first enrollment,
  the response also includes the authenticator setup secret and provisioning
  URI after password authentication; no session is established until the
  authenticator code is verified.
- `POST /api/v1/auth/mfa/totp/verify` accepts `challenge_id` and the current
  authenticator code or an unused recovery code.
- `POST /api/v1/auth/mfa/verify` retains the SMS challenge flow used by Django
  admin authentication and any existing SMS challenge consumer.
- Password recovery uses `POST /api/v1/auth/password-reset` and
  `POST /api/v1/auth/password-reset/confirm`. Reset links use Django's
  single-use password-reset token. Local `DEBUG=1` sends recovery and tenant
  activation emails through Mailpit (`EMAIL_HOST`/`EMAIL_PORT`; local UI at
  `http://localhost:8025`). Deployed environments use Brevo and require
  `PUBLIC_SITE_URL`, `BREVO_API_KEY`, and `BREVO_SENDER_EMAIL`. The request
  response does not reveal whether an account exists. Local Compose sets
  `PUBLIC_SITE_URL` from `WEBSITE_HOST_PORT` (default `3001`); if the website
  uses a different host port, activation and recovery emails use that port.
- `GET /api/v1/auth/me` returns the current user's role names and accessible
  branches. `POST /api/v1/auth/logout` ends the session.
- Landlord and caretaker profile and onboarding requests use the same Django
  session. The portal-specific endpoints are `GET/PATCH
  /api/v1/{landlord,caretaker}/profile` and `GET/POST
  /api/v1/{landlord,caretaker}/onboarding`; profile updates validate the email
  and Kenyan mobile number, and onboarding/tutorial state is stored on the
  Django `UserProfile`.
- Landlord signup is not publicly available through the website or API.
  Authorized Property Officers can create landlord records and portal accounts
  for their assigned branches. The landlord receives an activation email and
  must activate the account before signing in. During onboarding, an existing
  unassigned property in the selected branch can optionally be linked to the
  landlord; property registration can also select an already-created landlord.
- Public tenant self-registration at `POST /api/v1/auth/register-tenant`
  creates a tenant-role Django account and session, but deliberately does not
  create a tenant, lease, or unit assignment. Until the agency links an
  approved tenancy, the website shows an awaiting-linkage resident dashboard.
  A move-in tenancy can issue a tenant activation link after the move-in
  invoice is fully paid. Caretakers can complete a property-scoped invitation
  created by authorized property staff or the property's landlord; unrestricted
  caretaker signup is not offered.
- The client-facing rental search reads `GET /api/v1/public/vacancies`, which
  returns only explicitly listed vacant units and a reduced public-safe set of
  property fields. It does not expose internal branch IDs or private unit
  records.
- Caretaker invitations are scoped to properties the authenticated landlord
  or Property Officer can manage. The emailed activation link and six-digit
  code are both required; only their hashes are stored. Invitations expire
  after `AUTH_DIRECTOR_ACTIVATION_TTL_SECONDS` (15 minutes by default), allow
  at most five failed code attempts, and are consumed once when the caretaker
  account and durable property assignment are created. Authorized staff capture
  the caretaker's name, email, phone, ID or registration number, and optional
  KRA PIN when inviting them; these details are stored on the profile when the
  caretaker activates. The API does not return the raw activation link or code.
- Tenant activation for a Property Officer move-in package is sent by email
  only after confirmed allocations fully pay the move-in invoice. Pending or
  bounced cheques do not count as payment. The email contains a one-time link
  and six-digit code, both of which are required; only their hashes are
  stored. Activation credentials expire after 48 hours, are invalidated when
  replaced, and successful activation returns the tenant to sign in rather
  than creating a session. An authorized staff member can resend the email.
- For active tenancies without a move-in package, the legacy tenant activation
  API still returns a one-time token and code to authorized staff; its
  code-only verification flow remains available. Tenant activation challenges
  expire after 48 hours and are invalidated when a new challenge is issued for
  the same tenant and unit.
- The landlord move-in workflow creates a provisional tenant record only after
  the move-in balance and deposits are fully paid, then sends a Django
  activation link. New move-in tenants must provide a national ID or passport
  during activation; the tenant record is not considered complete or included
  in ordinary tenant lists until that requirement is met.
- MFA SMS messages are rendered from the `login_verification_code` template
  and queued in the encrypted `SMSDelivery` outbox. Run
  `python manage.py dispatch_sms_deliveries` from a scheduled worker to send
  queued messages. Failed deliveries are retried with backoff and eventually
  recorded as failed; expired MFA messages are discarded without sending.
- The available placeholder templates are `login_verification_code` (`code`),
  `payment_receipt` (`reference`, `amount`, `agency_name`), and
  `invoice_reminder` (`amount`, `agency_name`). Only MFA is currently wired to
  an application workflow; the others are ready for their relevant billing
  events.
- The SMS adapter is selected by `AUTH_SMS_BACKEND`. The Softlink adapter sends
  JSON requests over HTTPS to `SOFTLINK_SMS_API_URL`, authenticates with the
  secret `SOFTLINK_SMS_API_KEY`, and reuses a stable `Idempotency-Key` for
  delivery retries. Set `SOFTLINK_SMS_TRAFFIC_CLASS` according to the provider's
  account policy. The development-only
  console backend is selected when `DEBUG=1`; it refuses to send when debug
  mode is disabled. Production has no fallback provider and finance-role login
  returns `503` without creating a session until one is configured. The Softlink adapter will be wired when its API
  configuration and integration details are provided.
- Delivery attempts and provider references are visible in the read-only
  `SMSDelivery` Django admin to finance-role staff. Queue, outcome, and admin
  log-view events are audited. Message bodies, verification codes, and full
  recipient numbers are encrypted at rest and cleared after terminal delivery
  states. Encryption keys derive from `DJANGO_SECRET_KEY`; drain queued
  messages before rotating that setting.
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

Authenticator-app verification applies to Super Admin (Director), Branch
Manager, and Accountant. Django superusers also require MFA because their
privileges exceed those role groups; Django admin currently retains its SMS
verification flow.

## Branch API access

`GET /api/v1/branches` and `GET /api/v1/branches/{branch_id}` derive access
from the signed-in user's server-side branch assignments. Requests without a
session return `401`; an unavailable or out-of-scope branch returns `404`.
Super Admin (Director) can access all seeded branches.
