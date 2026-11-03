# ADR 0001: Docker Compose on a VM for staging

- Status: Accepted
- Date: 2026-11-03

## Context

The project contains k3s and Helm infrastructure intended for a later
production delivery target. The current staging environment is a single
Namecheap VPS with Docker and Docker Compose already installed. Staging needs
to validate the Django API, Next.js website, PostgreSQL, Redis, MinIO, and
Caddy HTTPS routing without introducing Kubernetes operational overhead before
the application and deployment contracts are stable.

Staging must use fake or demo data only. Production records, credentials, and
the live Supabase database must not be copied into this environment.

## Decision

Deploy staging as a Docker Compose application on the prepared VM. The
Compose project runs the application services and their staging-only
dependencies, while Caddy terminates HTTPS for
`staging.legacyhomes.live`. Runtime secrets remain in
`/srv/legacy-real-estate/.env` on the VM and are not committed to the
repository.

The GitHub Actions staging workflow uploads only committed source from the
`staging` branch, starts the Compose project, runs Django migrations and
static-file collection, and verifies the public health endpoint.

## Consequences

### Positive

- Fits the existing single-VM staging capacity and operational skills.
- Keeps PostgreSQL, Redis, and MinIO private inside the Compose network.
- Provides a repeatable deployment path without requiring a Kubernetes
  control plane.
- Allows HTTPS callback testing against a stable public staging hostname.

### Negative

- The VM is a single staging failure domain.
- Compose does not provide Kubernetes-level scheduling, rollout, or
  self-healing guarantees.
- Backups, rollback, resource limits, and demo-data reset procedures remain
  operational responsibilities.

## Revisit criteria

Reconsider k3s/Helm when production requires multiple nodes, automated
rollouts, horizontal scaling, stronger workload isolation, or a supported
high-availability strategy.
