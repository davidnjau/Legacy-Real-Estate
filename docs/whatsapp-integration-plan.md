# WhatsApp Bot Integration Plan

Target branch: `feature/collaborator-base`. Provider: Meta WhatsApp Cloud API
(already provisioned — WABA `1491412959505603`, phone number ID
`1329145410293089`, display name "Legacy Homes", `payment_confirmation`
template approved).

## Goals

Add WhatsApp as a first-class channel alongside SMS and email:

1. **Phase 1 — Notifications**: every outbound SMS template also delivered on
   WhatsApp, driven by the existing `ArrearsReminder` / delivery-queue
   infrastructure.
2. **Phase 2 — Tenant self-service**: conversational flows for balance,
   receipts, and photo-based maintenance reporting, backed by the existing
   tenant REST endpoints.
3. **Phase 3 — Landlord/staff flows and in-chat rent payment**: portfolio
   views, quotation approvals, and M-PESA STK push initiated from WhatsApp
   with tri-channel receipts.

## Architecture

### Placement

A new Django app `apps/core/whatsapp/`:

```
whatsapp/
  views.py        # webhook endpoint (GET verify, POST events)
  client.py       # Cloud API client (send text/template/interactive, fetch media)
  router.py       # conversation state machine
  flows/          # one module per role: tenant.py, landlord.py, agent.py, director.py
  models.py       # WhatsAppContact, WhatsAppDelivery, ConversationState
  templates.py    # template catalog (named-variable bodies + param builders)
```

Outbound messages follow the proven `SMSDelivery` pattern (`users/sms.py`):
a `WhatsAppDelivery` row is queued, a Celery task dispatches with retry and
exponential backoff, payloads encrypted at rest with Fernet, full audit trail.
The Cloud API client is a pluggable backend like `SMSBackend`, with a
`ConsoleWhatsAppBackend` for local dev.

### Webhook

`POST /api/v1/whatsapp/webhook` (plus GET for Meta's verification handshake).
Requirements:

- Validate `X-Hub-Signature-256` (HMAC of raw body with the app secret).
- Respond 200 immediately; process the event in a Celery task (Meta retries
  on slow/non-2xx responses).
- Deduplicate on message `wamid` (store processed ids; senders retry).
- Statuses (`sent`/`delivered`/`read`/`failed`) update `WhatsAppDelivery`;
  failures with code 131042 (billing) and 131049 (frequency cap) must alert
  staff — these are silent drops otherwise (learned in provisioning).

### New models

| Model | Fields | Purpose |
|---|---|---|
| `WhatsAppContact` | `user` FK nullable, `tenant` FK nullable, `landlord` FK nullable, `wa_id` (unique, encrypted), `id_number_hash`, `verified_at`, `status` | Links a WhatsApp number to platform identities after ID verification |
| `WhatsAppDelivery` | mirror of `SMSDelivery`: encrypted recipient + payload, template name, status, provider_message_id (`wamid`), attempts, error_code | Outbound queue + audit |
| `ConversationState` | `contact` FK, `active_role`, `state` (JSON), `expires_at` | Where the user is in a flow (role chosen, awaiting photo, awaiting description, STK pending) |

## Identity & onboarding

### Onboarding message

Wherever tenant/landlord onboarding currently sends email/SMS (activation,
welcome), also queue a WhatsApp **template** message (business-initiated
messages outside the 24h window require an approved template):

> Welcome to {{agency_name}}! To manage your account on WhatsApp, reply to
> this message and verify with your national ID number.

### Verification flow

1. User messages the business number (any text, e.g. "hello" / "hey").
2. If `wa_id` has no verified `WhatsAppContact`: bot asks for the national ID
   number.
3. Django matches the ID number against saved records (`Tenant.id_number`,
   landlord/staff profiles). Match → create verified `WhatsAppContact`
   linking `wa_id` to every identity that ID holds. No match → polite
   failure; after 5 failed attempts lock the `wa_id` for 24h (rate limit —
   ID numbers are guessable).
4. Comparison uses a normalized hash; never store or log the raw ID number in
   bot logs. Optional hardening (phase 3+): if the `wa_id` differs from the
   phone on record, send a one-time code via the saved SMS number.

### Role navigation (multi-role)

After verification (and on every "hello"), resolve the contact's roles:

- **One role** → straight into that role's menu.
- **Multiple roles** (e.g. tenant **and** landlord) → interactive button/list
  message: "How would you like to continue today?" → Tenant / Landlord /
  Agent / Director. Selection stored in `ConversationState.active_role`;
  a persistent "Switch role" row appears in every main menu.

Role menus (interactive list messages):

| Role | Menu rows |
|---|---|
| Tenant | Rent balance · Pay rent · Report an issue · My receipts · Switch role |
| Landlord | Portfolio summary · Pending approvals · Remittances · Statements · Switch role |
| Agent / Property Officer | My branch arrears · Unmatched payments · Open tickets · Switch role |
| Director | Cross-branch summary · Approvals queue · Switch role |

All data access goes through the existing branch-scoped service layer —
the bot is a thin client over the same authorization rules as the portals.

## Phase 1 — Notifications

Map the existing SMS template catalog (`users/sms.py`) to Meta-approved
WhatsApp templates. Meta constraints learned during provisioning (apply to
every template submission):

- This WABA uses **named variables**: `{{amount}}`, `{{invoice_ref}}` —
  numbered `{{1}}` is rejected.
- A body may not **start or end with a variable** (trailing punctuation does
  not count — end with real words, e.g. "Thank you.").
- Every variable needs a realistic **sample value** for review.
- Category `UTILITY` for all transactional notices; OTP-style messages must
  use `AUTHENTICATION` (Meta writes those bodies).

Initial catalog (template name → trigger):

| Template | Trigger |
|---|---|
| `payment_confirmation` (approved) | `PaymentAllocation` created |
| `invoice_issued` | Invoice moves Draft → Issued |
| `arrears_reminder` | `ArrearsReminder` scheduler, per stage |
| `maintenance_update` | Ticket status change |
| `moveout_notice_ack` | `MoveOutNotice` received |
| `water_reading` | Meter reading billed |

Wiring: `ArrearsReminder.channel` already defines `whatsapp` — the scheduler
gains a channel fan-out that queues `WhatsAppDelivery` next to `SMSDelivery`.
Recipient preference order: WhatsApp if a verified `WhatsAppContact` exists,
SMS otherwise (both during the rollout period).

## Phase 2 — Tenant self-service

Read flows call the existing endpoints with the identity resolved from
`WhatsAppContact` (server-side service calls, not HTTP round-trips):

- **Rent balance** → tenant dashboard data (open invoices, totals, due dates).
- **My receipts** → recent payments; each receipt as a formatted message.
- **Report an issue** → see below.

### Photo-based maintenance reporting (detailed)

**Conversation flow**

1. Tenant taps **Report an issue** (or types "repair", "leak", …).
2. Bot sends an interactive **list** of `MaintenanceTicket` categories
   (plumbing, electrical, structural, …) sourced from the model's category
   choices.
3. Bot asks for a one-line description (free text).
4. Bot: "Please send up to 5 photos of the issue. Send *done* when finished,
   or *skip* if you have no photos." `ConversationState.state` enters
   `awaiting_photos` with the draft ticket payload.
5. Each incoming **image message** while in `awaiting_photos`:
   - Webhook delivers `{type: "image", image: {id, mime_type, sha256}}` —
     no bytes; the media must be fetched.
   - Celery task: `GET /{media_id}` → short-lived CDN URL (**valid ~5
     minutes** — fetch immediately, never store the URL) → download with the
     system-user token → validate MIME (`image/jpeg`, `image/png`) and size
     (Cloud API caps images at 5 MB) → stream into the existing
     MinIO/S3 private bucket using the same key scheme as portal-uploaded
     ticket photos → attach to the draft.
   - Reply per photo: "📷 Photo 1 received."
   - Dedup on media `sha256` (users double-send); hard cap 5 photos.
6. On *done*/*skip* (or 3 media-free minutes): create the
   `MaintenanceTicket` via the same service used by
   `POST /api/v1/tenant/maintenance-tickets/create` — property/unit derived
   from the tenancy, photos already attached, source recorded as `whatsapp`.
7. Confirmation reports that the request was logged for review and includes
   the ticket number, property and unit, category, priority, issue summary, and
   the number of photos attached (or that no photos were attached). The tenant
   is asked to keep the ticket number for reference; the message does not
   promise that a caretaker has already been assigned.
8. **Thread bridging**: ticket comments (`/maintenance-tickets/<id>/comments`)
   flow both ways — staff portal comments are queued to WhatsApp (template if
   outside the 24h window); tenant replies while a ticket is open append to
   the comment thread.

**Edge cases**

- Image arrives with no `awaiting_photos` state → ask whether to open a new
  ticket (buttons: "Report an issue" / "Main menu").
- Media download failure → retry 3× with backoff, then tell the tenant that
  photo failed and to resend.
- Multiple open tickets → "status" lists them as an interactive list.
- Abandoned draft (state expiry, 24h) → discard silently.

**Security**

- Only verified contacts with an **active tenancy** may create tickets; the
  unit is always derived server-side — the tenant never specifies it.
- Photos land in the private bucket with the same ACLs as portal uploads;
  WhatsApp media ids/URLs are never persisted.
- Every action writes `AuditLog` (actor = linked user, source = whatsapp).

## Phase 3 — Landlord/staff flows & in-chat rent payment

### Landlord / agent / director

- **Portfolio summary** → landlord dashboard aggregates.
- **Pending approvals** → maintenance quotations as one message each with
  **Approve / Reject** buttons → quotation decision endpoint. Approvals above
  a configurable amount require portal confirmation (bot deep-links to it).
- **Remittances / statements** → read-only summaries.
- Waiver requests and unmatched-payment alerts for accountants reuse the
  Phase 1 notification rails.

### STK push from WhatsApp (detailed)

1. Tenant: **Pay rent** → bot shows open invoice(s) with balances; tenant
   picks one (or "pay full balance").
2. Bot: "An M-PESA prompt for KES {{amount}} will appear on this phone.
   Enter your PIN to complete." → initiates STK push via the existing Daraja
   integration (same path as `/move-in-settlements/stk-payment`), with
   `account_reference` = the invoice/tenancy reference so the existing
   matcher can allocate automatically. `ConversationState` enters
   `stk_pending` with the `CheckoutRequestID`.
3. Daraja callback (existing `receive_mpesa_callback`) stores the
   `MpesaCallback`, matches to a `Payment`, allocates to the invoice.
4. On successful allocation, dispatch the receipt **tri-channel**:
   - **WhatsApp**: `payment_confirmation` template (amount, reference).
   - **SMS**: existing payment receipt template.
   - **Email**: if the tenant has an email on record, via Brevo.
   Each channel queues through its own delivery model; one failure never
   blocks the others.
5. Failure/timeout callback (cancelled, wrong PIN, timeout) → WhatsApp
   message with a **Try again** button. `stk_pending` expires after 3
   minutes with a status check against Daraja's query API.

## Configuration

Environment (Django settings, same pattern as SMS/Brevo):

```
WHATSAPP_ACCESS_TOKEN        # permanent System User token
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_WABA_ID
WHATSAPP_APP_SECRET          # webhook signature validation
WHATSAPP_VERIFY_TOKEN        # webhook handshake
WHATSAPP_BACKEND             # cloudapi | console
```

## Rollout

1. Phase 1 behind a per-branch feature flag; templates submitted and
   approved before any code ships (review takes minutes–hours, occasionally
   24h).
2. Pilot with one branch's tenants; monitor `WhatsAppDelivery` failure codes
   (especially 131042/131049) and quality rating before fan-out.
3. Business verification should be completed in parallel — the account is
   capped at 250 business-initiated conversations/day until then, which a
   full tenant roster will exceed.
4. Webhook moves off the demo tunnel to the production host as part of
   Phase 1 deployment; the `override_callback_uri` currently pointing at the
   demo must be replaced (or removed) at cutover.

## Testing

- Unit: router state machine (verification, role switch, photo collection,
  STK pending) with recorded webhook payloads as fixtures.
- Integration: replay captured Meta deliveries against a staging webhook;
  M-PESA sandbox for the STK loop.
- The demo bot (`whatsapp_test.py`, `rental_demo.py`, `whatsapp_bot.py` in
  the provisioning workspace) doubles as a reference client for payload
  shapes.
