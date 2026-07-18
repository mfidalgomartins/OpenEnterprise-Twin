# Contributing

OpenEnterprise Twin accepts focused changes that preserve reproducibility, physical/accounting coherence and evidence-linked decisions. Keep patches small enough to review and avoid unrelated refactors.

## Development setup

Required locally:

- Python 3.12
- Node.js 22 or newer supported by the current frontend lockfile
- Docker with Compose
- Make

```bash
cp .env.example .env
make install
make dev
```

`make dev` starts PostgreSQL, applies migrations, seeds the versioned Northstar baseline and serves API plus frontend. Stop it with `Ctrl+C`; the PostgreSQL volume remains available for the next run.

The supported commands are:

| Command | Purpose |
| --- | --- |
| `make dev` | Full local stack with migration and seed |
| `make demo` | Paired flagship experiment through the public API |
| `make lint` | Ruff, mypy, import boundaries, ESLint and TypeScript |
| `make test` | Backend tests except the long performance marker, plus Vitest |
| `make build` | Backend wheel and production frontend bundle |
| `make docker-build` | Production backend and frontend images |
| `make e2e` | Isolated SQLite API plus responsive, full-stack and print Playwright gates |

## Code rules

- Python uses snake_case, explicit types, immutable Pydantic domain values and UTF-8.
- Keep domain and simulation free of FastAPI, SQLAlchemy and frontend contracts. `lint-imports` enforces this.
- Generate randomness only in the stochastic-tape module. Business transitions must consume named draws and must not use global random state.
- Use integer cents/minutes/units for ledger state; make rounding boundaries explicit.
- Validate external inputs and use stable error or invariant codes.
- Prefer reusable functions and vectorized aggregation, but do not obscure auditable period logic for micro-optimizations.
- Frontend changes follow `docs/design-system.md`: horizontal navigation, editorial hierarchy, visible uncertainty and accessible exact-value evidence.
- Do not add placeholders, filler copy, dead modules or parallel implementations of an existing path.

## Change workflow

1. Read the relevant domain contract, tests and [model reference](model.md).
2. Write a failing test for behavior changes and confirm it fails for the intended reason.
3. Implement the smallest coherent change.
4. Run targeted tests, then `make lint`, `make test` and `make build`.
5. Review `git diff` for generated files, unrelated changes and stale documentation.

Do not commit `.env`, virtual environments, `node_modules`, wheels, `dist`, simulation `artifacts`, coverage, caches, Playwright reports or test results. The repository `.gitignore` covers the standard locations.

## Model changes

A model change must document:

- equation and units;
- deterministic rounding behavior;
- stochastic distribution and draw key, if any;
- expected impact on invariants and metrics;
- whether company, scenario, engine or tape version must change;
- a determinism test and relevant conservation/reconciliation tests.

Changing random process names, entities, draw IDs, distribution mapping or draw order can alter reproducibility. Preserve stable keys unless the tape version changes. Scenario comparisons must continue to use common random numbers and compatible horizons.

## Database changes

PostgreSQL is the production relational contract. Add a reversible Alembic revision, use the repository naming convention, and verify both directions against PostgreSQL 16:

```bash
make db
cd backend
../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m alembic downgrade -1
../.venv/bin/python -m alembic upgrade head
```

Do not put complete simulation traces in transactional tables. Store immutable large evidence through the artifact-store boundary and persist its digest. Treat persisted JSON as a versioned schema: add a narrow legacy upgrade path and prove idempotent reads when its shape changes.

## API changes

Keep public resources under `/api/v1`. Validate payloads with strict Pydantic models, return `202` plus `Location` for asynchronous experiment creation, preserve idempotency semantics and map failures to `application/problem+json`. Never expose stack traces or unbounded execution controls.

Contract changes require integration tests for success, validation, missing resources, idempotency and lifecycle failure. PostgreSQL-backed migration/API smoke runs separately in CI from isolated SQLite tests.

## Plugin changes

New capabilities need an immutable typed input/output protocol, a manifest kind, compatibility validation and registry tests for duplicates, incompatible versions and incorrect implementations. A plugin must not receive a database session, FastAPI request or mutable engine state. Entry-point discovery is not part of 0.1.

## Pull-request checklist

- [ ] Scope is focused and architecture boundaries still pass.
- [ ] New behavior was test-driven; determinism and failure paths are covered.
- [ ] Equations, units, assumptions and version implications are documented.
- [ ] Migration is reversible when persistence changes.
- [ ] UI conclusions cite computed evidence and expose uncertainty.
- [ ] `make lint`, `make test` and `make build` pass.
- [ ] No secrets, local state or generated artifacts are included.
- [ ] Limitations and compatibility effects are stated explicitly.
