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
  there are Branch Manager, Accountant, Property Officer / Agent, Land Sales
  Agent. A staff account may hold multiple agency roles; caretaker access is
  provisioned separately through property-scoped invitations. Director,
  landlord, and tenant access use their own role-specific records.
- After sign-in, users with multiple portal roles are sent to a workspace
  chooser; users with one role continue directly to its dashboard. Portal
  headers expose workspace switching when more than one role is available.
- `GET/POST /api/v1/director/staff` lists or creates staff for an `agency_id`.
  `PATCH/DELETE /api/v1/director/staff/<membership_id>` updates role, branch
  scope, or active status, or suspends that agency membership.
  `POST /api/v1/director/staff/<membership_id>/resend-activation` issues a new
  activation code for a pending account or role link. Branch assignments are
  validated against the Director's agency. Suspending staff disables only that agency
  membership; it does not disable a login or its memberships in other agencies.
- `GET/POST /api/v1/director/branches` lists or creates branches for an
  `agency_id`. Only an authenticated Director with an active membership in
  that agency can manage its branches. Newly created branches appear in the
  Director staff list and can be assigned to staff memberships.
- A new staff login is inactive and password-disabled until the invitee submits
  the one-time email OTP and a new password to
  `POST /api/v1/auth/staff/activate`. Staff OTPs are hashed at rest, expire
  after 15 minutes by default, and are invalidated after five failed attempts
  or a successful activation. To add staff access to an existing active
  account, the Director selects that account from the agency-scoped search;
  exact identity and contact details must match before the role and branch
  scope are linked immediately. No activation OTP is issued and the existing
  password is preserved. Email delivery failures for new or pending accounts
  leave the request pending so the Director can retry with Resend activation.
- The Director, agency staff, tenant, and caretaker activation forms share the
  same website password checks: at least 12 characters with lowercase,
  uppercase, number, and symbol, plus matching confirmation. Django password
  validation is also applied by the activation endpoints.
- The Next.js website proxies browser authentication requests to these Django
  endpoints, so the browser receives the Django `sessionid` and `csrftoken`
  cookies without exposing backend credentials or maintaining a second portal
  session.
- Staff sign in with their staff email and password. Tenants and landlords
  use a Kenyan mobile number and a six-digit SMS code instead of a password.
  Store mobile numbers in international format without a leading plus, using
  `2547XXXXXXXX` or `2541XXXXXXXX`.
- Browser clients use Django session cookies. Fetch a CSRF token from
  `GET /api/v1/auth/csrf` and send it as `X-CSRFToken` on unsafe requests.
- Staff password sessions expire after eight hours by default. Configure the
  positive lifetime in seconds with `AUTH_SESSION_AGE_SECONDS`. Tenant and
  landlord phone-code sessions are limited to the same browser/device and
  expire after 12 hours by default; configure that lifetime with
  `AUTH_TENANT_SESSION_AGE_SECONDS`. A phone-code session for an account that
  also has staff roles is restricted to tenant/landlord APIs. Opening a staff
  workspace requires a fresh staff sign-in; Director and finance roles still
  complete mandatory authenticator verification.
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
- `POST /api/v1/auth/login` accepts a staff `identifier` and `password`. A
  successful non-finance login returns the authenticated user. Director and
  finance-role login returns `202` with an opaque TOTP challenge ID. During
  first enrollment, the response also includes the authenticator setup secret
  and provisioning URI after password authentication; no staff session is
  established until the authenticator code is verified.
- `POST /api/v1/auth/phone-otp/request` accepts a phone number and queues a
  six-digit SMS code for a linked active tenant or landlord account.
  `POST /api/v1/auth/phone-otp/verify` accepts its challenge ID and code.
  Sign-in codes expire after five minutes, allow five attempts, and can be
  resent once every 60 seconds. The API response does not reveal whether the
  phone number belongs to an active account.
- `POST /api/v1/auth/mfa/totp/verify` accepts `challenge_id` and the current
  authenticator code or an unused recovery code.
- `POST /api/v1/auth/mfa/verify` retains the SMS challenge flow used by Django
  admin authentication and any existing SMS challenge consumer.
- Password recovery uses `POST /api/v1/auth/password-reset` and
  `POST /api/v1/auth/password-reset/confirm`. Reset links use Django's
  single-use password-reset token. Local `DEBUG=1` sends recovery emails
  through Mailpit (`EMAIL_HOST`/`EMAIL_PORT`; local UI at
  `http://localhost:8025`). Staging uses the Softlink email API configured by
  `EMAIL_DELIVERY_BACKEND=softlink`, `SOFTLINK_EMAIL_API_URL`,
  `SOFTLINK_EMAIL_API_KEY`, and `SOFTLINK_EMAIL_TIMEOUT_SECONDS`; requests
  include recipient addresses, a subject, and branded HTML in the `body` field.
  The HTML uses the website's forest-green palette and inline styles for email
  client compatibility. The API key must remain outside source control. Sender
  identity is configured and verified in the Softlink account, not by the
  idempotency header. Other deployed environments may continue to use Brevo
  with `BREVO_API_KEY` and `BREVO_SENDER_EMAIL`. The request response does not
  reveal whether an account exists. Local Compose sets
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
  for their assigned branches. A new landlord account receives a phone
  activation code and uses phone OTP to sign in. For an existing active
  account, the Property Officer selects the account from the agency-scoped
  search; an exact match of recorded name, email, phone, and ID/registration
  number links landlord access immediately without another OTP and preserves
  the existing password.
  During onboarding, an existing
  unassigned property in the selected branch can optionally be linked to the
  landlord; property registration can also select an already-created landlord.
- Public tenant self-registration is disabled. Tenant accounts are provisioned
  by an agency through the move-in or legacy-migration workflow.
  New tenant activations use an SMS code and the phone number recorded during
  onboarding; no email activation link or password is issued. Move-in
  activation codes expire after 48 hours. Caretakers can complete a property-scoped invitation
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
  account and durable assignments for the selected properties are created.
  These activation requirements apply to new or pending accounts. To add
  caretaker access to an existing active account, authorized staff select it
  from the agency-scoped search; exact name, email, phone, and identity details
  are checked and the property assignment is linked immediately without an
  activation challenge.
  For new invitees, authorized staff capture the caretaker's name, email,
  phone, ID or registration number, and optional KRA PIN; these details are
  stored on the profile when the caretaker activates. The API does not return
  the raw activation link or code.
- Tenant activation for a Property Officer move-in package is sent by SMS
  only after confirmed allocations fully pay the move-in invoice. Pending or
  bounced cheques do not count as payment. The tenant verifies the registered
  phone, six-digit code, and unit code; provisional move-in tenants must also
  provide the National ID/passport captured during onboarding. Activation
  codes expire after 48 hours, allow at most five failed attempts, and are
  invalidated when replaced. Successful activation creates or links a
  password-disabled account and returns the tenant to sign in rather than
  creating a session. An authorized staff member can resend the SMS.
- Existing accounts can be linked to a tenancy only when the active account
  has already been associated with the same agency and its recorded contact
  and identity details match the tenant KYC record. The Property Officer must
  explicitly select the account from the agency-scoped search. The tenant role
  is linked immediately without an activation code or password change. For a
  company tenant, the login identity matches the human contact person's
  account, while the tenant record keeps the company identity. New tenant
  activations require the ID/passport number recorded during onboarding to
  match.
- Staff who also hold a tenant account cannot perform sensitive tenancy
  lifecycle, financial, or approval actions on their own tenancy. Another
  authorized staff member must handle those actions.
- For active tenancies without a move-in package, the legacy tenant activation
  API queues an activation SMS to the registered phone; activation requires
  the phone and six-digit code, with unit code where supplied. It does not
  return a usable email activation link or accept password setup. The
  landlord move-in workflow creates a provisional tenant record only after
  the move-in balance and deposits are fully paid, then queues the SMS.
  New move-in tenants must provide a National ID or passport during
  activation; the tenant record is not considered complete or included in
  ordinary tenant lists until that requirement is met.
- Legacy migration is available to authorized Property Officers for preview
  and commit at any time.
  The website collects the Units and Tenancies CSVs sequentially and previews
  before commit. Import dates accept `DD/MM/YYYY` and ISO `YYYY-MM-DD`.
  Occupied migrated tenants receive SMS activation codes valid for 72 hours;
  still-valid challenges are not resent on repeated imports, and failed SMS
  delivery expires the challenge so a later import can retry. The commit is
  performed atomically without a secondary Accountant approval.
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
