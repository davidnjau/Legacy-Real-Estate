# API conventions

The canonical API contract is
[`packages/contracts/openapi.yaml`](../packages/contracts/openapi.yaml).
Update the contract first, regenerate `@legacy/api-client`, and include the
generated-file check in the same change.

## Versioning and compatibility

- Public routes are prefixed with `/api/v1`.
- Additive changes are preferred within a version. Do not remove or rename
  fields, change their meaning, or narrow accepted values in a compatible
  release.
- Introduce incompatible request or response changes in a new version.
- Keep operation IDs stable and unique. Document request parameters,
  response shapes, and error responses in OpenAPI.

## Authentication and authorization

- Protected operations use the Django `sessionid` cookie. Obtain a CSRF token
  from `GET /api/v1/auth/csrf`; send the returned value in `X-CSRFToken` on
  state-changing requests, with cookies enabled for cross-origin browser
  requests.
- Operations are protected by default. An operation is public only when its
  OpenAPI operation explicitly sets `security: []`.
- Enforce authorization on the server for every request. Apply role,
  branch, property, unit, and tenant scope as appropriate; never rely on
  client-side checks.
- Return `401` for missing or invalid credentials and `403` for an
  authenticated caller who lacks permission. Avoid leaking whether
  out-of-scope records exist.
- The health endpoint is public and reveals only whether the API process is
  responding. Do not expose secrets, personal data, or dependency status
  from it.

## Errors

- Return errors as `application/problem+json`, following RFC 9457.
- Use the shared `Problem` shape and the actual HTTP status code in
  `status`. Include a stable `type` and concise `title`; provide safe
  `detail`, `instance`, and `request_id` when available.
- Use `ValidationProblem` for field-level validation failures. Identify
  request fields with JSON Pointers where practical.
- Use `400` for malformed requests, `401` for unauthenticated requests,
  `403` for forbidden actions, `404` for unavailable resources, `409` for
  state conflicts, `422` for semantically invalid values, and `429` for
  rate limits.
- Never return stack traces, credentials, internal SQL, or sensitive
  personal/financial data in an error response.

## Pagination

- Paginated collection endpoints accept `limit` and `offset`. Default
  `limit` is 50; the maximum is 100; `offset` defaults to 0 and cannot be
  negative.
- Return a `pagination` object with `limit`, `offset`, `count`, and `total`.
- Use deterministic ordering with a unique tie-breaker so adjacent pages
  neither skip nor duplicate records when the underlying data is unchanged.
- Document any endpoint-specific filters and ordering in its OpenAPI
  operation.

## Audit and sensitive operations

- This initial contract has no business-data mutations or financial actions.
  The public health operation has no side effects and requires no audit
  event. Authentication outcomes are recorded as described in
  [`authentication.md`](./authentication.md).
- Future operations that change access, tenancy, money, payment allocation,
  deposits, remittances, or other sensitive state must define server-side
  authorization and an audit event. Record actor, action, target, time,
  outcome, and a correlation/request ID without copying secrets into the
  audit record.
- Financial records are corrected through reversal or adjustment workflows,
  not hard deletion. Approval and maker-checker requirements are specified
  by the approved business rules for each operation.

## Local checks

Run the contract linter, regenerate client types, verify the generated
output is current, run the API-client type check, and run Django tests:

```bash
pnpm --filter @legacy/contracts lint
pnpm gen
pnpm --filter @legacy/api-client test
cd apps/core && .venv/bin/python manage.py test
```
