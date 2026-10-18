# ADR-0002: Contract-first API with a generated client

- Status: Accepted
- Date: 2026-10-18

## Context

The Next.js website and the Laravel backend need a shared understanding of
request/response shapes (health check, tenancy statements, public unit
listings, the M-Pesa C2B callback). Hand-written client code drifts from the
backend over time, and the website needs to run in a fully mocked mode when
the backend isn't available.

## Decision

Treat `packages/contracts/openapi.yaml` (OpenAPI 3.1) as the single source
of truth. `packages/api-client` generates TypeScript types and a typed
client from it using `openapi-typescript` + `openapi-fetch`, plus MSW mock
handlers built from the examples in the spec. The generated client is never
hand-edited. CI (`packages.yml`) lints the contract with Spectral, runs an
`oasdiff` breaking-change check against `main`, regenerates the client, and
fails if the committed generated files differ from the regenerated output.

## Consequences

- The website can run against `mock`, `local`, or `dev` APIs by switching
  `NEXT_PUBLIC_API_MODE`, with no backend required for mock mode.
- Breaking API changes are caught in CI before merge, not at runtime.
- Contributors must edit `openapi.yaml` and regenerate, rather than editing
  generated client code directly.
