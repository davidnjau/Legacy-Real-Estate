# Legacy Real Estate

Monorepo for the Legacy Real Estate Agency platform: property management,
rent collection and land sales for a Kenyan agency with Nairobi and Nakuru
branches.

## Repository structure

This repository uses the module layout already agreed by the team:

```text
apps/
  core/       Django API, business logic, workers and scheduler
  website/    Next.js application for the public site and role-based portals
packages/
  contracts/  OpenAPI contract and integration/event schemas
  api-client/ Generated TypeScript client and mocks
  ui/         Shared UI components and styles
database/
  schema/     Database reference material and schema documentation
  seeds/      Safe development and staging seed data
  anonymise/  Scripts for producing safe staging data
  cluster/    PostgreSQL cluster and connection-pool configuration
  backup-restore/ Backup, restore and recovery procedures
infra/
  terraform/  Cloud infrastructure definitions
  k3s/        Development server provisioning
deploy/
  charts/       Helm charts
  platform/     Shared Kubernetes components
  environments/ Environment-specific deployment values
tools/
  local/     Local Docker Compose stack
  scripts/   Repository automation
docs/
  srs/       Requirements and product specification
  adr/       Architecture decisions
  runbooks/  Operational procedures
.github/     Pull request templates, code ownership and CI workflows
```

The frontend application in `apps/website` has been fully migrated from its
initial Supabase prototype to canonical Django-backed API routes and Django
session authentication. All portal routes (Landlord, Tenant/Resident, Caretaker,
Agency Staff), billing, move-in/move-out workflows, and ledgers now communicate
exclusively with the Django backend in `apps/core` backed by PostgreSQL. The
original Supabase schema is retained as a reference at
`database/schema/legacy-supabase-phase-one.sql` for historical reference.

## Technology

- Backend: Django and Python.
- Database: PostgreSQL.
- Web application: Next.js and React, including responsive role portals
  and the public website.
- Integration processing: RabbitMQ-backed Celery workers and Beat handle
  scheduled billing and water-reading workflows; audited SMS delivery remains
  queued through the Django SMS outbox.
- Files: S3-compatible object storage is integrated for private tenant
  maintenance evidence; remaining file workflows are not yet migrated.
- Hosting: Kubernetes is a later delivery target, not configured by this
  initial setup branch.

## Local development

Requirements for the full containerized stack: Docker and Docker Compose.
Node.js 20+, pnpm 9+, and Python 3.12+ are needed only when running the
website or backend directly on the host.

```bash
make up
```

Compose starts the website and installs workspace dependencies from the
root pnpm lockfile in isolated container volumes. It reuses the host
Corepack cache (`~/.cache/node/corepack` by default; override with
`COREPACK_CACHE_DIR` if needed) and the ignored `.pnpm-store` cache to avoid
re-downloading pnpm and workspace packages on every start. Do not also run
`pnpm dev` while the Compose website service is running.

The Django health endpoint is `http://localhost:8000/api/v1/health`; the
Next.js application is served at `http://localhost:3000`. Mailpit is
available at `http://localhost:8025`, the MinIO S3 API at
`http://localhost:9000`, and the MinIO console at `http://localhost:9001`.
Sign in to the local console with access key `localminio` and secret key
`local-only-change-before-sharing`. These disposable credentials are for
local development only. The local Compose ports bind to loopback only.
Compose builds the pinned MinIO Community release from source because its
official project no longer publishes prebuilt Community container images.
The Compose website uses webpack mode because Turbopack cannot resolve the
Next.js package in this isolated pnpm workspace layout.

Stop the services with `make down`; inspect their output with `make logs`.
Compose uses disposable local-only credentials and a Redis volume configured
with append-only persistence. Do not reuse those credentials or this local
stack for staging or production.

### Local RabbitMQ credentials

The local stack uses a dedicated RabbitMQ account for Django/Celery rather than
the broker's `guest` account. Before the first `make up`, create or edit the
ignored repository-root `.env` file and set:

```dotenv
RABBITMQ_DEFAULT_USER=legacy_worker
RABBITMQ_DEFAULT_PASS=choose-a-long-local-password
RABBITMQ_ERLANG_COOKIE=choose-a-long-random-cookie
```

Use URL-safe letters, numbers, hyphens, or underscores in the local username
and password because Compose also builds the Celery AMQP URL from these values. The Compose defaults
are for local development only. Start the stack with `make up`; RabbitMQ's
management UI is at `http://localhost:15672`. The same credentials and cookie
must be supplied through the staging/production secret manager, never copied
from local defaults.

RabbitMQ's default-user settings initialize an empty data directory; they do
not replace accounts in an existing `rabbitmq_data` volume. For an existing
volume, add the chosen account and grant it access before switching clients:

```bash
docker compose -f tools/local/docker-compose.yml exec rabbitmq \
  rabbitmqctl add_user legacy_worker 'choose-a-long-local-password'
docker compose -f tools/local/docker-compose.yml exec rabbitmq \
  rabbitmqctl set_permissions -p / legacy_worker '.*' '.*' '.*'
```

If that user already exists, use `rabbitmqctl change_password` instead of
`add_user`, then apply the same permissions command.

```bash
docker compose -f tools/local/docker-compose.yml exec rabbitmq \
  rabbitmqctl change_password legacy_worker 'choose-a-long-local-password'
```

Set the same account values in `.env`, then run `make up` so Compose recreates
the broker clients. Do not run `docker compose down -v`: that deletes the
RabbitMQ data volume. For a fresh install, the initialization variables create
the account automatically. Keep the Erlang cookie stable for an initialized
broker; changing it is not a way to rotate the broker user's password.

The public homepage, all role dashboards, and local Compose startup run
exclusively against the Django backend and PostgreSQL database, without any
Supabase dependency. Copy `apps/website/.env.example` to `.env.local` inside
`apps/website` and supply local environment overrides if needed; never commit
actual environment files or credentials.

PostgreSQL is published on host port 55432 by default to avoid conflicts with
a host-installed PostgreSQL service; the Django container still uses port
5432 inside the Compose network. If the PostgreSQL or website host port
conflicts, override it, for example:

```bash
POSTGRES_HOST_PORT=55433 WEBSITE_HOST_PORT=3001 make up
```

When running Django directly on the host, use
`apps/core/.env.example` as the template; its PostgreSQL port matches the
default Compose host port. Docker Compose uses a separate local PostgreSQL
database. Do not configure the new Django service to use production data until
a reviewed migration and cutover plan is approved.

## API contract

The shared contract is [`packages/contracts/openapi.yaml`](packages/contracts/openapi.yaml).
Follow [the API conventions](docs/api-guidelines.md) when adding operations.
Generated API-client types are not hand-edited. Follow the
[authentication foundation](docs/authentication.md) and run
`pnpm --filter @legacy/contracts lint`, `pnpm gen`, and
`pnpm --filter @legacy/api-client test` before submitting contract changes.

The SRS entity list, relationships, and currently specified data conventions
are recorded in the [domain model and data dictionary](docs/data-dictionary.md).

## Git workflow

Before deploying, confirm the VPS runtime file
`/srv/legacy-real-estate/.env` exists with restrictive permissions and contains
the variables in `tools/staging/.env.example`. In particular, RabbitMQ needs
`RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, and
`RABBITMQ_ERLANG_COOKIE`; use unique, strong, URL-safe values and keep them
only in the VPS `.env` (not in GitHub or this repository). Generate password
and cookie values with `openssl rand -hex 32`, and keep the cookie stable after
RabbitMQ has initialized its data volume. If RabbitMQ already has a populated
volume, its default-user environment variables do not create or rotate users;
add or update the configured broker user with `rabbitmqctl` before starting
Celery.

The staging deployment runs Django migrations automatically. PostgreSQL uses
the named `postgres_data` volume, which remains across normal deployments and
the workflow's release-directory replacement. Back up the database before
deploying schema changes, and do not use `docker compose down -v` or remove
the PostgreSQL volume unless intentionally resetting staging data. Keep
staging data anonymized or synthetic.

Staging email is captured in Mailpit rather than delivered to real recipients.
The authenticated inbox is available at
`https://staging.legacyhomes.live/mailpit/`; set `MAILPIT_UI_AUTH` in the VPS
`.env` to a unique `username:password` value before deploying. Generate a
password with `openssl rand -hex 32`, and do not commit or share the credential.
Mailpit's SMTP and UI ports are not published directly; Caddy proxies the UI
path, and messages are retained for 14 days by default. Activation links and
other email contents are visible to anyone with inbox access, so restrict the
credential to trusted staging operators.

The GitHub Actions `staging` environment must also have `STAGING_HOST`,
`STAGING_USER`, and `STAGING_SSH_KEY` configured for the deployment workflow.
These are SSH deployment secrets; the application and RabbitMQ runtime
credentials remain on the VPS.

For the current staging-led delivery flow, work may be committed locally and
pushed directly to the `staging` branch:

```bash
git status --short
git diff --check
git add <intended-files>
git diff --cached
git commit -m "feat: complete wave 2 platform foundation"
git push origin staging
```

The push triggers the staging deployment workflow. To deploy another branch
without merging it into `staging`, open **Actions → Deploy staging → Run
workflow**, select the branch, and check the confirmation before starting the
run. The workflow runs its checks before replacing the shared staging VPS.
Validate the deployed health endpoint and presentation site before promoting
the validated staging commit to `main`. Feature branches remain optional for
isolated experiments, parallel work, or changes that should not enter staging
yet. Keep `main` protected for approved releases. Never commit credentials,
production records, or local environment files.

## Prototype dashboards

Role-specific UX prototypes are documented in
[`docs/dashboards.md`](docs/dashboards.md). They are references, not
production application code.
