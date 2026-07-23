# Changelog

All notable changes to OpenEnterprise Twin are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
[0.3.2]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.3.2
[0.3.1]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.3.1
[0.3.0]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.3.0
[0.2.0]: https://github.com/mfidalgomartins/OpenEnterprise-Twin/releases/tag/v0.2.0
