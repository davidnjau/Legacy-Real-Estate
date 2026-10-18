# legacy-platform

Monorepo for the Legacy Real Estate Agency platform: property management,
rent collection through M-Pesa, and land sales for a Kenyan agency with
Nairobi and Nakuru branches.

> This repo is currently a **Phase 1 skeleton** — directory structure and
> root tooling only. Application code, infrastructure manifests, and CI
> workflows are added in later phases.

## Structure

```
legacy-platform/
├── apps/
│   ├── core/            # Laravel: back-office, portals, callback API, queue workers, scheduler
│   └── website/         # Next.js public listings site
├── packages/
│   ├── contracts/       # openapi.yaml (API contract), job/event schemas, sample M-Pesa payloads
│   ├── api-client/      # generated TS client + MSW mocks (never hand-edited)
│   └── ui/              # shared UI components/styles
├── database/
│   ├── schema/          # ERD + data dictionary
│   ├── seeds/           # fake-data generators
│   ├── anonymise/       # scripts to make a production copy safe for staging
│   ├── cluster/         # CloudNativePG Cluster manifest, PgBouncer Pooler, replication settings
│   └── backup-restore/  # backup, restore, PITR scripts + drill checklist
├── infra/
│   ├── terraform/       # managed K8s, Postgres, Redis, object storage, NAT, DNS (validate only)
│   └── k3s/             # Ansible playbook for the single-node dev server
├── deploy/
│   ├── charts/          # Helm charts (core, website)
│   ├── platform/        # ingress-nginx, cert-manager, KEDA, CloudNativePG operator, kube-prometheus-stack, Loki
│   └── environments/    # per-env values (dev, staging, production)
├── tools/
│   ├── local/           # docker-compose.yml for local dev
│   └── scripts/
├── docs/                # srs/, adr/, runbooks/
└── .github/             # workflows, CODEOWNERS, PR template, dependabot
```

## Quick start

```bash
pnpm install
make up        # start core, website, postgres, redis, mailpit, minio
make gen       # regenerate the API client from packages/contracts/openapi.yaml
pnpm dev       # runs gen:watch + the website together
```

`curl localhost:8000/api/v1/health` should return OK once `apps/core` exists.

## API modes

The website selects its API source via `NEXT_PUBLIC_API_MODE`:

- `mock` — MSW handlers generated from `packages/contracts/openapi.yaml`, no backend required
- `local` — `http://localhost:8000/api/v1`
- `dev` — `https://dev.<AGENCY_DOMAIN>/api/v1`

## Environments

| Environment | Infra                                   | Notes                                  |
| ----------- | ---------------------------------------- | --------------------------------------- |
| dev         | Single-node k3s (`infra/k3s/`)           | 8 vCPU / 16 GB node, 1 replica per app  |
| staging     | Managed Kubernetes (`infra/terraform/`)  | Small sizing                            |
| production  | Managed Kubernetes (`infra/terraform/`)  | Full HA sizing per SRS 17.6             |

## CI

Workflows are path-filtered per module (`core.yml`, `website.yml`,
`packages.yml`, `database.yml`, `infra.yml`, `deploy.yml`, `docs.yml`), so a
PR only triggers checks for the modules it actually touches. See
`.github/workflows/` and `docs/adr/` for details once later phases land.
