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

The current rental application was moved into `apps/website` as a starting
point. It still contains its previous Supabase-backed API routes while the
new Django API is established. New backend work belongs in `apps/core`; do
not point the new Django service at the live Supabase database or run the
legacy SQL file against it. The original Supabase schema is retained as a
reference at `database/schema/legacy-supabase-phase-one.sql` for planning a
verified data migration.

## Technology

- Backend: Django and Python.
- Database: PostgreSQL.
- Web application: Next.js and React, including responsive role portals
  and the public website.
- Integration processing: Redis and Celery are planned; queue workers are
  not part of this initial scaffold.
- Files: S3-compatible object storage is planned.
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

The public homepage and local Compose startup do not require Supabase
credentials. Legacy authentication and data-backed pages still need a
local/test Supabase project, not production. Copy
`apps/website/.env.example` to `.env.local` inside `apps/website` and supply
credentials through a local secret store; never commit actual environment
files or credentials.

If Docker Desktop cannot publish the default PostgreSQL or website host
ports, use `POSTGRES_HOST_PORT=5433 WEBSITE_HOST_PORT=3001 make up`.
PostgreSQL and Next.js continue to use ports 5432 and 3000 inside the Compose
network. Use `apps/core/.env.example` as the template for local Django
settings. Docker Compose uses a separate local PostgreSQL database. Do not
configure the new Django service to use production data until a reviewed
migration and cutover plan is approved.

## API contract

The shared contract is [`packages/contracts/openapi.yaml`](packages/contracts/openapi.yaml).
Follow [the API conventions](docs/api-guidelines.md) when adding operations.
The contract currently covers the health and session-authentication
endpoints; generated API-client types are not hand-edited. Follow the
[authentication foundation](docs/authentication.md) and run
`pnpm --filter @legacy/contracts lint`, `pnpm gen`, and
`pnpm --filter @legacy/api-client test` before submitting contract changes.

The SRS entity list, relationships, and currently specified data conventions
are recorded in the [domain model and data dictionary](docs/data-dictionary.md).

## Git workflow

Create short-lived `feature/<name>` branches from `develop`, open pull
requests targeting `develop`, and merge only after required checks and
review pass. Keep `main` protected for approved releases. Never commit
credentials, production records, or local environment files.

## Prototype dashboards

Role-specific UX prototypes are documented in
[`docs/dashboards.md`](docs/dashboards.md). They are references, not
production application code.
