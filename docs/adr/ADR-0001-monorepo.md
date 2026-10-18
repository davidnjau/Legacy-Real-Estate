# ADR-0001: Single monorepo for the Legacy platform

- Status: Accepted
- Date: 2026-10-18

## Context

The Legacy Real Estate Agency platform spans a Laravel backend (back-office,
portals, callback API, queue workers, scheduler), a Next.js public listings
website, shared API contracts/client packages, database tooling, and
infrastructure/deploy manifests. These pieces evolve together: a change to
the OpenAPI contract affects the backend, the generated client, and the
website in the same unit of work.

## Decision

Use a single GitHub repository (`legacy-platform`) with pnpm workspaces and
Turborepo to orchestrate tasks (`dev`, `build`, `lint`, `test`, `gen`) across
`apps/*` and `packages/*`. Infrastructure, deploy, and database tooling live
alongside the application code in the same repo, with path-filtered CI
workflows per module (`core.yml`, `website.yml`, `packages.yml`,
`database.yml`, `infra.yml`, `deploy.yml`, `docs.yml`) so unrelated modules
don't block each other.

## Consequences

- One PR can atomically change the contract, the generated client, and the
  consumers of that contract.
- CODEOWNERS and path-filtered CI keep review and check scope aligned to the
  module actually touched, despite the shared repo.
- Requires discipline around path filters and reusable workflows to avoid
  CI running (and costing) more than necessary on unrelated changes.
