# Task 4 Report: bounded operational metrics and API security headers

## Status

Completed and committed after focused TDD, full test-suite verification, static checks, and self-review.

## Delivered scope

- Added `OperationalMetrics`, an in-process lock-protected HTTP accumulator.
  - Its only metric key is `(method, route, status_family)`.
  - Methods are limited to the standard API verbs or `OTHER`.
  - Registered Starlette route templates are used; paths without a matched route are recorded as `unmatched`.
  - Uptime and request duration use `time.monotonic()`.
  - Snapshots are copied under the lock and sorted by `(method, route, status_family)`.
- Stored one metrics instance on `app.state` in the application factory and added an outer HTTP metrics middleware.
- Added protected `GET /api/v1/system/metrics`, typed with Pydantic response models. The endpoint inherits the existing API-key security dependency and exposes only uptime plus aggregate request metrics.
- Added ASGI security-header middleware, outside the existing request pipeline, with:
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - `X-Frame-Options: DENY`
  - `Permissions-Policy: camera=(), geolocation=(), microphone=(), payment=(), usb=()`
  - `Cache-Control: no-store`
- Added unit tests for aggregation, deterministic ordering, and monotonic uptime; integration tests cover protected metrics, `unmatched` paths, identifier exclusion, and headers on normal, problem, 404, 413 body-rejection, and readiness responses.

## TDD evidence

1. RED: `../.venv/bin/python -m pytest tests/unit/api/test_observability.py tests/integration/test_system_api.py -q`
   - Failed at collection with `ModuleNotFoundError: No module named 'openenterprise_twin.api.observability'`.
2. Minimal accumulator implementation:
   - `../.venv/bin/python -m pytest tests/unit/api/test_observability.py -q`
   - Result: `3 passed in 0.01s`.
3. Integration RED before middleware and route implementation:
   - `../.venv/bin/python -m pytest tests/integration/test_system_api.py -q`
   - Result: two expected failures: metrics endpoint returned 404 and security headers were absent.
4. Integration GREEN after the app wiring, middleware, and endpoint:
   - `../.venv/bin/python -m pytest tests/unit/api/test_observability.py tests/integration/test_system_api.py -q`
   - Result: `12 passed in 0.53s`.
5. A first integration run exposed `AttributeError: 'OperationalMetrics' object has no attribute 'clock'` in the new middleware. Root cause was a call to a nonexistent public method before the existing trace middleware. Replacing it with local `time.monotonic()` calls was the single corrective change; the focused suite then passed.

## Final verification

| Command | Result |
| --- | --- |
| `../.venv/bin/python -m pytest tests -q` | `436 passed, 1 skipped` in 66.46 seconds; PostgreSQL integration skipped because its migrated PostgreSQL 16 CI service is unavailable. |
| `../.venv/bin/ruff check src tests` | `All checks passed!` |
| `../.venv/bin/python -m mypy src` | `Success: no issues found in 64 source files` |
| `git diff --check` | No whitespace errors |

## Self-review

- The snapshot and API models contain no principal, tenant, scenario instance, or trace identifier fields. The integration test confirms a supplied scenario identifier and the authenticated subject do not appear in the response.
- The metrics middleware compares `_route_path(scope)` with the immutable
  registered-route regexes. The raw request path is never stored as a metric
  label; no registered match produces `unmatched`.
- The security middleware is registered outermost, so it transforms normal responses, exception-handler problems, Starlette 404 responses, direct `RequestBodyLimitMiddleware` rejections, and readiness responses.
- The endpoint is part of the existing protected `system_router`; no separate authentication path was added.

## Concerns and known boundaries

- Metrics are intentionally process-local and reset on restart. They are suitable for a bounded operational endpoint, not long-term telemetry retention or cross-worker aggregation.
- A request to `/api/v1/system/metrics` is recorded after its snapshot is generated, so it appears in the next scrape rather than its own response. This avoids mutating the response while it is being sent.
- Requests rejected upstream of this FastAPI process (for example by a proxy or WAF) cannot be observed or header-modified by application middleware.

## Task 4 review fixes

Implemented the changes required by `task-4-review.md` without entering Task 5 scope.

- `OperationalMetrics` is now configured with an immutable registered-route-template set and enforces the bounded-label invariant inside `record_http()`:
  - supported methods are normalized to uppercase;
  - unsupported methods become `OTHER`;
  - routes outside the registered set become `unmatched`;
  - statuses outside 100–599 are ignored and create no bucket.
- Added a frozen `RegisteredRouteResolver` built from FastAPI's effective route contexts after all routers are included. It copies only registered templates, methods, and compiled path regexes.
- The metrics middleware resolves every request against that immutable resolver. An early 413 can therefore be attributed to `/api/v1/scenarios` before FastAPI sets `scope["route"]`; raw request paths are only compared and are never stored.
- Added negative unit coverage for arbitrary routes, unsupported and lowercase methods, and out-of-range statuses. Added unit coverage for included-router resolution and an integration regression for oversized `POST /api/v1/scenarios`.

### Review-fix TDD evidence

1. Accumulator RED: `tests/unit/api/test_observability.py` failed 7 tests because `registered_route_templates` was not yet supported.
2. Accumulator GREEN: `7 passed in 0.01s`.
3. Resolver RED: unit collection failed because `RegisteredRouteResolver` did not exist.
4. Early-413 RED: the regression failed with no `/api/v1/scenarios` 4xx bucket.
5. Final focused GREEN: `18 passed in 0.59s`.

### Review-fix final verification

| Command | Result |
| --- | --- |
| `../.venv/bin/python -m pytest tests -q` | `442 passed, 1 skipped` in 64.74 seconds; PostgreSQL integration skipped because its migrated PostgreSQL 16 CI service is unavailable. |
| `../.venv/bin/ruff check src tests` | `All checks passed!` |
| `../.venv/bin/python -m mypy src` | `Success: no issues found in 64 source files` |
| `git diff --check` | No whitespace errors |
