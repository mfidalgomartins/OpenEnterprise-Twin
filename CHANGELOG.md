# Changelog

All notable changes to OpenEnterprise Twin are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.1] - 2026-07-26

### Added

- **Governed decision actions in the Decision Ledger UI.** An analyst can now
  create a decision draft and advance it through the lifecycle, and an approver
  can approve a decision under review, directly from the executive frontend.
  Approval signs the exact `content_digest` the API serves, and separation-of-
  duties or optimistic-concurrency rejections surface as problem details.
- **Outcome recording and a recalibration CTA in the Monitoring Center.** A
  realised KPI outcome can be recorded from the UI, and when drift passes the
  recalibration threshold the report links straight to the Calibration Studio.
- `content_digest` is exposed on the decision snapshot response, so no client
  ever recomputes the digest an approver signs.

Together these close the governed loop inside the product: calibrate, optimise,
approve, implement, monitor and recalibrate without leaving the UI.

## [0.6.0] - 2026-07-26

### Added

- OIDC access-token validation with strict issuer, audience, lifetime,
  asymmetric algorithm and bounded JWKS contracts, including unknown-key
  refresh for rotation.
- Authorization-code + PKCE browser login, safe effective-session display,
  logout and role-aware navigation.
- Explicit `viewer`, `analyst`, `approver` and `admin` policy with
  identity-bound decision approval and server-side separation of duties.
- Tenant ownership across persisted business resources, mandatory
  tenant-scoped repositories, composite constraints and PostgreSQL
  cross-tenant proof.
- PostgreSQL-backed analytical jobs for experiments, calibration, optimization
  and adaptive comparison with idempotency, leases, heartbeat, progress,
  cancellation, retry, stale-worker rejection and restart recovery.
- A responsive Jobs workspace with workload filters, attempts, safe terminal
  problems, cancellation and result links.
- Operator documentation for OIDC registration, tenant bootstrap,
  service-account rotation, worker deployment and queue incidents.

### Changed

- Analytical submission APIs now return a consistent `202 Accepted` durable-job
  resource and `Location` instead of coupling expensive work to the request.
- Experiment execution now uses the same durable handler and result-link
  contract as the other analytical workloads.
- The Nginx edge no longer injects service-account API keys into browser
  traffic and can add one exact OIDC origin to CSP `connect-src`.
- Aligned backend, frontend and public release metadata at `0.6.0`.

### Security

- Added a live ephemeral OIDC browser gate covering PKCE login, bearer
  authorization, logout and role-denied approval.
- Added JWKS rotation, PostgreSQL tenant-matrix, concurrent worker claim and
  restart-recovery integration gates.
- Enforced a 500 kB JavaScript bundle budget while keeping locked npm and
  hash-pinned Python audits free of known vulnerabilities.

## [0.5.0] - 2026-07-26

### Added

- Distinct public `/health` liveness and `/ready` dependency-readiness
  contracts, with safe RFC 9457 failure responses.
- Protected `/api/v1/system/info` release metadata and
  `/api/v1/system/metrics` bounded process metrics.
- An operator runbook covering startup, migrations, probes, coherent
  database/artifact backup and restore, graceful shutdown, dependency audits,
  incident triage and rollback.

### Changed

- Replaced the frontend router with `wouter` while preserving the public route
  contract and locked reproducible build.
- Aligned backend, frontend and public release metadata at `0.5.0`.
- Refreshed the public architecture, security posture and evidence-backed
  enterprise roadmap without claiming later identity or distributed-storage
  phases.

### Security

- Removed known high-severity findings from the locked frontend dependency
  graph.
- Added no-store, anti-sniffing, anti-framing, no-referrer and restrictive
  browser-permission headers to API responses.
- Kept operational labels bounded to method, registered route template and
  status family, with unknown paths collapsed to `unmatched`.

## [0.4.1] - 2026-07-23

### Added
- **Calibration Studio CSV in the UI** — the executive frontend can now upload a
  long-format CSV (with a clear, problem-detail error state) and download the
  loaded dataset as formula-neutralised CSV, completing the v0.4.0 connector end
  to end. Calibration is dataset-aware: an uploaded dataset is calibrated
  without a backtest window it cannot cover.

[0.4.1]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.4.1

## [0.4.0] - 2026-07-23

### Added
- **CSV ingestion connector** — a long-format CSV (`period_date, series,
  entity_id, value, unit`) can be ingested through `POST /api/v1/datasets/csv`.
  Every field is validated strictly against the canonical model, with precise,
  line-numbered errors for unknown series, malformed dates or non-numeric
  values.
- **Formula-neutralised CSV export** — `GET /api/v1/datasets/{id}/export.csv`
  serialises a dataset to CSV and prefixes any cell that begins with a formula
  character (`=`, `+`, `-`, `@`), so a downloaded dataset is safe to open in a
  spreadsheet (CSV-injection defence).
- Documentation of the calibration, credibility, optimization and monitoring
  mathematics in the model reference, and this changelog.

## [0.3.2] - 2026-07-23

### Changed
- Gave the adaptive-policy rule a single source of truth so the declarative
  preview and the comparison request cannot drift.

### Tests
- Covered the decision-ledger request-error and monitoring no-outcomes states.

## [0.3.1] - 2026-07-23

### Fixed
- The policy optimizer de-duplicates decoded price changes by
  `(segment, product)`, avoiding an invalid `PolicyLevers` for overlapping
  price levers.
- The dataset observation cap moved into the calibration service, so every
  ingestion path — including synthetic generation — is bounded.
- Concurrent creation of a decision, dataset or calibration surfaces as a clean
  conflict instead of an unhandled error.

### Changed
- Consolidated the analytics content-addressing into one shared helper; single
  source of truth for editable decision states; tightened frontend types.

## [0.3.0] - 2026-07-23

### Added
- **Governed Decision Autopilot** — the closed decision loop: Calibration Studio
  (data quality, calibration, backtesting, credibility score), Policy Optimizer
  (constrained NSGA-II), Adaptive Policy Engine (safe declarative DSL), Decision
  Ledger (append-only governed state machine) and Monitoring Center (outcome
  reconciliation and drift).
- A pure `analytics` layer, closed-loop API and persistence (Alembic 0002/0003),
  five executive frontend sections, and the `make demo-autopilot` end-to-end
  demonstration.

## [0.2.0] - 2026-07-23

### Added
- Governed enterprise decision twin: deterministic Monte Carlo engine, paired
  scenario comparison, executive control tower, Pareto frontier and immutable
  executive briefs.

[0.4.0]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.4.0
[0.5.0]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.5.0
[0.6.1]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.6.1
[0.6.0]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.6.0
[0.3.2]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.3.2
[0.3.1]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.3.1
[0.3.0]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.3.0
[0.2.0]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.2.0
