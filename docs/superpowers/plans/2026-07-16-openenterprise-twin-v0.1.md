# OpenEnterprise Twin 0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible decision-intelligence vertical slice in which a user changes a Northstar Components policy, runs a Monte Carlo experiment, compares it with baseline and publishes an evidence-backed executive brief.

**Architecture:** A modular Python monolith owns domain, simulation, scenario, reporting and persistence boundaries behind a versioned FastAPI API. A React application presents the same typed experiment result as an interactive decision brief. PostgreSQL is the only relational datastore; simulation artifacts use a separate artifact-store port.

**Tech Stack:** Python 3.12, Pydantic 2, NumPy, FastAPI, SQLAlchemy 2, PostgreSQL 16, Alembic, pytest, Hypothesis, React 19, TypeScript, Vite, TanStack Query, React Router, Recharts, Vitest, Testing Library, Playwright, Docker Compose, GitHub Actions.

## Global Constraints

- Every simulation is reproducible from company-model version, scenario schema version, engine version, resolved assumptions, seed and iteration count.
- Domain and simulation modules cannot import FastAPI, SQLAlchemy or frontend contracts.
- PostgreSQL is the only supported relational datastore.
- Randomness must be generated outside business transitions as a versioned stochastic tape using stable draw keys; global random state is prohibited.
- Every simulation trace must pass non-negativity, physical flow-conservation and cash-reconciliation invariants.
- UI conclusions must reference computed evidence and expose uncertainty; single-point claims without provenance are prohibited.
- The product uses a horizontal shell, editorial hierarchy and narrative sections rather than a sidebar and KPI-card grid.
- Code, copy and documentation may not contain placeholders, filler text, dead modules or alternative implementations.

## File map

```text
openenterprise-twin/
├── .github/workflows/ci.yml               # backend, frontend and build gates
├── backend/
│   ├── pyproject.toml                      # Python package and tooling
│   ├── alembic.ini
│   ├── migrations/                         # PostgreSQL schema history
│   ├── src/openenterprise_twin/
│   │   ├── api/                            # FastAPI transport and wiring
│   │   ├── domain/                         # value objects, company and results
│   │   ├── simulation/                     # period engine and Monte Carlo
│   │   ├── scenarios/                      # use cases and comparison
│   │   ├── reporting/                      # deterministic brief model
│   │   ├── plugins/                        # extension protocols and registry
│   │   └── infrastructure/                 # persistence and artifact adapters
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── app/                            # shell and routes
│   │   ├── components/                     # reusable primitives and charts
│   │   ├── features/                       # decision room and scenarios
│   │   └── lib/                            # API client, tokens and formatting
│   └── tests/
├── docs/
│   ├── architecture.md
│   ├── model.md
│   └── contributing.md
├── docker-compose.yml
├── Makefile
├── LICENSE
└── README.md
```

---

### Task 1: Repository foundation and typed company model

**Files:**

- Create: `backend/pyproject.toml`
- Create: `backend/src/openenterprise_twin/__init__.py`
- Create: `backend/src/openenterprise_twin/domain/company.py`
- Create: `backend/src/openenterprise_twin/domain/scenario.py`
- Create: `backend/src/openenterprise_twin/domain/errors.py`
- Create: `backend/tests/unit/domain/test_company.py`
- Create: `backend/tests/unit/domain/test_scenario.py`
- Create: `.gitignore`
- Create: `LICENSE`

**Interfaces:**

- Produces: `CompanyModel`, `Product`, `CustomerSegment`, `Plant`, `FinancialPolicy`, `Scenario`, `PolicyLevers`.
- Produces: immutable Pydantic models with `extra="forbid"` and bounded numeric fields.

- [x] **Step 1: Write domain validation tests**

```python
def test_company_rejects_non_positive_capacity(northstar_company):
    payload = northstar_company.model_dump()
    payload["plant"]["regular_capacity_hours"] = 0
    with pytest.raises(ValidationError):
        CompanyModel.model_validate(payload)


def test_policy_levers_reject_impossible_rates():
    with pytest.raises(ValidationError):
        PolicyLevers(price_change=Decimal("-1.01"))
```

- [x] **Step 2: Run tests and confirm the models do not exist**

Run: `cd backend && python -m pytest tests/unit/domain -q`  
Expected: collection fails because `openenterprise_twin.domain` is unavailable.

- [x] **Step 3: Implement the immutable domain models**

Use `ConfigDict(frozen=True, extra="forbid")`, `Decimal` for monetary rates and explicit constraints: rates in `[-1, 10]`, probabilities in `[0, 1]`, physical quantities strictly positive where required.

- [x] **Step 4: Verify domain tests and static checks**

Run: `cd backend && python -m pytest tests/unit/domain -q && python -m ruff check src tests && python -m mypy src`  
Expected: all commands succeed.

- [x] **Step 5: Commit**

```bash
git add .gitignore LICENSE backend
git commit -m "feat: establish typed enterprise domain"
```

### Task 2: Northstar reference model and deterministic period engine

**Files:**

- Create: `backend/src/openenterprise_twin/domain/results.py`
- Create: `backend/src/openenterprise_twin/simulation/engine.py`
- Create: `backend/src/openenterprise_twin/simulation/shocks.py`
- Create: `backend/src/openenterprise_twin/simulation/demand.py`
- Create: `backend/src/openenterprise_twin/simulation/operations.py`
- Create: `backend/src/openenterprise_twin/simulation/finance.py`
- Create: `backend/src/openenterprise_twin/simulation/invariants.py`
- Create: `backend/src/openenterprise_twin/simulation/reference.py`
- Create: `backend/tests/unit/simulation/test_engine.py`
- Create: `backend/tests/unit/simulation/test_invariants.py`

**Interfaces:**

- Consumes: `CompanyModel`, `Scenario`, `ShockTape`.
- Produces: `simulate_trace(company, scenario, shock_tape) -> SimulationTrace`.
- Produces: `validate_trace(trace) -> None`, raising `InvariantViolation` with a stable code.

- [x] **Step 1: Write deterministic and conservation tests**

```python
def test_same_seed_produces_identical_trace():
    company = build_northstar_company()
    scenario = build_baseline_scenario()
    tape = build_shock_tape(company, seed=20260716, replication_id=731)
    assert simulate_trace(company, scenario, tape) == simulate_trace(
        company, scenario, tape
    )


def test_shipments_never_exceed_available_orders_and_backlog(simulation_trace):
    for period in simulation_trace.periods:
        assert period.shipped_units <= period.opening_backlog + period.new_orders
```

- [x] **Step 2: Confirm tests fail before implementation**

Run: `cd backend && python -m pytest tests/unit/simulation -q`  
Expected: collection fails on missing simulation modules.

- [x] **Step 3: Implement the period transition**

Implement the fixed daily order defined by the design specification. Demand uses segment/product elasticity, correlated AR(1) multipliers and negative-binomial arrivals; operations allocate finished goods, material and capacity; finance recognizes shipment revenue and settles working capital. Business transitions consume the supplied immutable stochastic tape and cannot generate random values.

- [x] **Step 4: Implement and enforce invariants**

Required checks: non-negative inventory/backlog/cash-flow components, shipment flow conservation, production bounded by material and effective hours, and closing-cash reconciliation within `Decimal("0.01")`.

- [x] **Step 5: Verify the engine**

Run: `cd backend && python -m pytest tests/unit/simulation -q`  
Expected: deterministic and invariant tests pass.

- [x] **Step 6: Commit**

```bash
git add backend/src/openenterprise_twin backend/tests/unit/simulation
git commit -m "feat: add reproducible enterprise simulation"
```

### Task 3: Monte Carlo experiments and risk summaries

**Files:**

- Create: `backend/src/openenterprise_twin/simulation/experiment.py`
- Create: `backend/src/openenterprise_twin/simulation/metrics.py`
- Create: `backend/tests/unit/simulation/test_experiment.py`
- Create: `backend/tests/performance/test_experiment_benchmark.py`

**Interfaces:**

- Consumes: `simulate_trace` and validated scenario inputs.
- Produces: `run_experiment(request: ExperimentRequest) -> ExperimentResult`.
- Produces: `MetricDistribution(mean, median, p5, p10, p90, p95, standard_deviation, breach_probability, cvar95)`.

- [x] **Step 1: Write aggregation and reproducibility tests**

```python
def test_experiment_exposes_required_percentiles(experiment_result):
    cash = experiment_result.metrics["closing_cash"]
    assert cash.p5 <= cash.p10 <= cash.median <= cash.p90 <= cash.p95
    assert 0 <= cash.breach_probability <= 1


def test_experiment_seed_is_reproducible(experiment_request):
    assert run_experiment(experiment_request) == run_experiment(experiment_request)
```

- [x] **Step 2: Confirm tests fail**

Run: `cd backend && python -m pytest tests/unit/simulation/test_experiment.py -q`  
Expected: module import fails.

- [x] **Step 3: Implement vector-friendly aggregation**

Generate stable stochastic tapes using a counter-based NumPy `Philox` generator, execute traces with bounded parallelism, reject invalid traces and aggregate `revenue`, `ebitda`, `free_cash_flow`, `closing_cash`, `otif`, `cancellation_rate`, `backlog_units`, `capacity_utilization`, `peak_revolver` and `rescue_funding`.

- [x] **Step 4: Add the 1,000 × 24 benchmark**

Run: `cd backend && python -m pytest tests/performance/test_experiment_benchmark.py -q --benchmark-disable`  
Expected: the functional benchmark completes and returns 1,000 valid 515-day iterations; CI records elapsed time without using a brittle hard failure threshold.

- [x] **Step 5: Commit**

```bash
git add backend/src/openenterprise_twin/simulation backend/tests
git commit -m "feat: quantify scenario uncertainty"
```

### Task 4: Scenario comparison and deterministic executive brief

**Files:**

- Create: `backend/src/openenterprise_twin/scenarios/comparison.py`
- Create: `backend/src/openenterprise_twin/reporting/brief.py`
- Create: `backend/src/openenterprise_twin/reporting/narrative.py`
- Create: `backend/tests/unit/scenarios/test_comparison.py`
- Create: `backend/tests/unit/reporting/test_brief.py`

**Interfaces:**

- Produces: `compare_experiments(baseline, candidate) -> ScenarioComparison`.
- Produces: `build_executive_brief(comparison) -> ExecutiveBrief`.
- The brief contains recommendation, outcome deltas, mechanisms, constraints, downside triggers, assumptions and provenance.

- [x] **Step 1: Write evidence-linkage tests**

```python
def test_recommendation_cites_metric_evidence(comparison):
    brief = build_executive_brief(comparison)
    assert brief.recommendation.evidence_metric_ids
    assert set(brief.recommendation.evidence_metric_ids) <= set(comparison.metrics)


def test_liquidity_breach_prevents_unqualified_recommendation(comparison_with_cash_risk):
    brief = build_executive_brief(comparison_with_cash_risk)
    assert brief.decision_status == "conditional"
    assert "closing_cash" in brief.recommendation.evidence_metric_ids
```

- [x] **Step 2: Implement delta, materiality and guardrail logic**

Materiality uses metric-specific thresholds stored in the company model. Comparisons use replication-level paired differences from common random numbers and expose confidence intervals and joint-improvement probabilities. A recommendation is `adopt`, `conditional` or `do_not_adopt`; template clauses are selected only from computed result states.

- [x] **Step 3: Verify reporting tests**

Run: `cd backend && python -m pytest tests/unit/scenarios tests/unit/reporting -q`  
Expected: all tests pass and no snapshot contains unsupported prose.

- [x] **Step 4: Commit**

```bash
git add backend/src/openenterprise_twin/scenarios backend/src/openenterprise_twin/reporting backend/tests/unit
git commit -m "feat: turn experiments into decision briefs"
```

### Task 5: Plugin contracts and compatibility registry

**Files:**

- Create: `backend/src/openenterprise_twin/plugins/protocols.py`
- Create: `backend/src/openenterprise_twin/plugins/manifest.py`
- Create: `backend/src/openenterprise_twin/plugins/registry.py`
- Create: `backend/tests/unit/plugins/test_registry.py`

**Interfaces:**

- Produces: `DemandModel`, `OperationsModel`, `FinanceModel`, `RiskMetric`, `OptimizationStrategy`, `ReportSection` protocols.
- Produces: `PluginRegistry.register(manifest, capability)` and `PluginRegistry.resolve(capability_id)`.

- [x] **Step 1: Test duplicate and incompatible registrations**

```python
def test_registry_rejects_duplicate_capability(plugin_registry, demand_plugin):
    plugin_registry.register(demand_plugin.manifest, demand_plugin)
    with pytest.raises(DuplicateCapabilityError):
        plugin_registry.register(demand_plugin.manifest, demand_plugin)
```

- [x] **Step 2: Implement manifests and registry**

Validate semantic versions, engine compatibility, configuration schema and unique capability identifiers. Plugins receive typed inputs only; no persistence session or FastAPI object is exposed.

- [x] **Step 3: Verify plugin tests and package boundaries**

Run: `cd backend && python -m pytest tests/unit/plugins -q && python -m import_linter`  
Expected: tests pass and forbidden infrastructure imports are absent from domain and simulation.

- [x] **Step 4: Commit**

```bash
git add backend/src/openenterprise_twin/plugins backend/tests/unit/plugins backend/pyproject.toml
git commit -m "feat: define safe extension contracts"
```

### Task 6: PostgreSQL repositories, experiment lifecycle and FastAPI

**Files:**

- Create: `backend/src/openenterprise_twin/infrastructure/settings.py`
- Create: `backend/src/openenterprise_twin/infrastructure/database.py`
- Create: `backend/src/openenterprise_twin/infrastructure/models.py`
- Create: `backend/src/openenterprise_twin/infrastructure/repositories.py`
- Create: `backend/src/openenterprise_twin/infrastructure/artifacts.py`
- Create: `backend/src/openenterprise_twin/api/app.py`
- Create: `backend/src/openenterprise_twin/api/dependencies.py`
- Create: `backend/src/openenterprise_twin/api/errors.py`
- Create: `backend/src/openenterprise_twin/api/schemas.py`
- Create: `backend/src/openenterprise_twin/api/routes.py`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/versions/0001_initial.py`
- Create: `backend/tests/integration/test_api.py`
- Create: `docker-compose.yml`

**Interfaces:**

- Implements the `/api/v1` resources from the design specification.
- Produces stable `application/problem+json` errors with `code`, `detail`, `trace_id` and field violations.
- Experiment creation returns `202 Accepted`, `Location` and a durable lifecycle state.

- [x] **Step 1: Write the end-to-end API contract test**

```python
def test_create_run_compare_and_report(api_client):
    scenario = api_client.post("/api/v1/scenarios", json=valid_scenario()).json()
    response = api_client.post(
        f"/api/v1/scenarios/{scenario['id']}/experiments",
        json={"iterations": 100, "seed": 731},
    )
    assert response.status_code == 202
    experiment = wait_for_experiment(api_client, response.headers["Location"])
    assert experiment["status"] == "completed"
    assert api_client.get(f"/api/v1/experiments/{experiment['id']}/report").status_code == 200
```

- [x] **Step 2: Implement persistence and reversible migration**

Persist scenarios, experiment lifecycle, metric summaries, briefs and provenance. Store full traces through `ArtifactStore`; the filesystem adapter writes content-addressed gzip JSON outside transaction tables.

- [x] **Step 3: Implement bounded in-process execution**

Use an application-level executor behind an `ExperimentRunner` protocol. Enforce limits on horizon, iterations and concurrent runs. Preserve stable lifecycle states: `queued`, `running`, `completed`, `failed`.

- [x] **Step 4: Verify migration and API contracts**

Run: `docker compose up -d db && cd backend && alembic upgrade head && python -m pytest tests/integration -q`  
Expected: migration succeeds against PostgreSQL 16 and all contract tests pass.

- [x] **Step 5: Commit**

```bash
git add backend docker-compose.yml
git commit -m "feat: expose durable scenario experiments"
```

### Task 7: Visual concept, design system and application shell

**Files:**

- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/AppShell.tsx`
- Create: `frontend/src/app/routes.tsx`
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/global.css`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/format.ts`
- Create: `frontend/src/components/BrandMark.tsx`
- Create: `frontend/src/components/Status.tsx`
- Create: `frontend/tests/AppShell.test.tsx`

**Interfaces:**

- Produces a horizontal shell with `Briefing`, `Twin`, `Scenarios`, `Decisions` and `Reports` destinations.
- Produces shared semantic tokens for canvas, ink, muted, line, decision, comparison and risk.

- [ ] **Step 1: Generate and approve the complete primary-screen visual concept**

The concept must show the decision sentence, outcome canvas, sticky decision rail, impact/mechanism/sensitivity chapters and responsive continuation. Extract the final tokens and component anatomy into `tokens.css` before component implementation.

- [ ] **Step 2: Write shell behavior tests**

```tsx
it("keeps executive navigation visible without a sidebar", () => {
  render(<AppShell><div>Decision content</div></AppShell>);
  expect(screen.getByRole("navigation", { name: /primary/i })).toBeVisible();
  expect(screen.getByText("Scenarios")).toBeVisible();
  expect(screen.queryByTestId("sidebar")).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Implement the shell and design primitives**

Use Inter Variable, 44px minimum interactive targets, 150–250ms state transitions, visible focus and reduced-motion support. No uppercase eyebrow, decorative badge, gradient, glass effect or generic metric-card family is permitted.

- [ ] **Step 4: Verify frontend unit tests**

Run: `cd frontend && npm run test -- --run && npm run typecheck && npm run lint`  
Expected: all commands succeed.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: establish executive decision interface"
```

### Task 8: Scenario comparison decision room

**Files:**

- Create: `frontend/src/features/scenarios/types.ts`
- Create: `frontend/src/features/scenarios/ScenarioComparePage.tsx`
- Create: `frontend/src/features/scenarios/DecisionHeader.tsx`
- Create: `frontend/src/features/scenarios/OutcomeTrajectory.tsx`
- Create: `frontend/src/features/scenarios/OutcomeSummary.tsx`
- Create: `frontend/src/features/scenarios/DecisionRail.tsx`
- Create: `frontend/src/features/scenarios/MechanismSection.tsx`
- Create: `frontend/src/features/scenarios/SensitivitySection.tsx`
- Create: `frontend/src/features/scenarios/EvidenceSection.tsx`
- Create: `frontend/tests/ScenarioComparePage.test.tsx`

**Interfaces:**

- Consumes: `GET /api/v1/experiments/{id}/comparison` and `/report`.
- Produces route: `/scenarios/:scenarioId/compare?experiment={experimentId}`.

- [ ] **Step 1: Write narrative and accessibility tests**

Test that recommendation, baseline/candidate uncertainty, constraints and provenance render from the API fixture; the chart has a text summary and exact-value table; recalculation state uses `aria-live="polite"`.

- [ ] **Step 2: Implement the analytical narrative**

Use a 1fr/320px desktop grid, a conclusion-led trajectory with P10–P90 band, three inline outcomes, sticky decision rail and chapters separated by whitespace and rules. At mobile widths, place the decision rail after the headline and retain all evidence.

- [ ] **Step 3: Browser-verify all breakpoints**

Run: `cd frontend && npm run dev -- --host 127.0.0.1` and inspect at 1440×1000, 1024×900 and 390×844.  
Expected: no clipped copy, hidden material risk, horizontal scrolling or overlapping chart labels.

- [ ] **Step 4: Commit**

```bash
git add frontend/src frontend/tests
git commit -m "feat: deliver the scenario decision room"
```

### Task 9: Scenario builder and experiment progress

**Files:**

- Create: `frontend/src/features/scenarios/ScenarioBuilder.tsx`
- Create: `frontend/src/features/scenarios/PolicyLever.tsx`
- Create: `frontend/src/features/scenarios/ExperimentProgress.tsx`
- Create: `frontend/src/features/scenarios/useScenarioExperiment.ts`
- Create: `frontend/tests/ScenarioBuilder.test.tsx`

**Interfaces:**

- Consumes: scenario creation and experiment lifecycle endpoints.
- Produces an autosaved, validated policy editor that retains the last valid result while recalculating.

- [ ] **Step 1: Test policy validation and retained results**

Test price, capacity and safety-stock boundaries; changed-driver count; submission payload; progress announcement; and retention of the last completed comparison during a new run.

- [ ] **Step 2: Implement scenario branching and recalculation**

Group levers by commercial, operations, supply and finance. Every lever shows unit, baseline, changed value and direct mechanism. Preserve inputs on errors and display stable API error codes with corrective action.

- [ ] **Step 3: Verify integration behavior**

Run: `cd frontend && npm run test -- --run ScenarioBuilder`  
Expected: validation, lifecycle and error-state tests pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/scenarios frontend/tests
git commit -m "feat: add auditable policy experimentation"
```

### Task 10: Executive report route and print output

**Files:**

- Create: `frontend/src/features/reports/ExecutiveReportPage.tsx`
- Create: `frontend/src/features/reports/report.css`
- Create: `frontend/tests/ExecutiveReportPage.test.tsx`
- Create: `frontend/e2e/report.spec.ts`

**Interfaces:**

- Consumes: immutable `ExecutiveBrief`.
- Produces: `/reports/:experimentId` and landscape A4 print layout.

- [ ] **Step 1: Test frozen provenance and complete report sections**

Assert recommendation, scenario comparison, value bridge, operational feasibility, sensitivities, actions, assumptions, model version, timestamp and experiment identifier are present.

- [ ] **Step 2: Implement the report using shared semantic components**

Use one conclusion per print page, fixed header/footer, exact-value tables and `break-inside: avoid`. Reports render stored snapshots and never refetch a newer experiment version.

- [ ] **Step 3: Verify browser and print rendering**

Run: `cd frontend && npm run e2e -- report.spec.ts`  
Expected: route renders, print media is applied and all eight report chapters are present.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/reports frontend/tests frontend/e2e
git commit -m "feat: publish immutable executive briefs"
```

### Task 11: GitHub presentation, operations and release gates

**Files:**

- Create: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/model.md`
- Create: `docs/contributing.md`
- Create: `Makefile`
- Create: `.env.example`
- Create: `.github/workflows/ci.yml`
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `frontend/e2e/scenario.spec.ts`

**Interfaces:**

- Produces: `make dev`, `make test`, `make lint`, `make demo` and `make build`.
- Produces: a CI gate covering backend, frontend, migration, production builds and E2E.

- [ ] **Step 1: Implement one-command local operation**

`make dev` starts PostgreSQL, migrates, seeds Northstar, starts API and frontend. `make demo` creates the flagship scenario and prints its URL and reproducibility identifiers.

- [ ] **Step 2: Write final documentation**

README order: product thesis, decision-room visual, five-minute demo, why it is different, architecture, model credibility, extension model, roadmap, limitations, contributing and licence. Model documentation includes equations, units, stochastic assumptions and invariant definitions.

- [ ] **Step 3: Implement CI**

Run backend lint/type/test, frontend lint/type/test, PostgreSQL integration tests, production builds and Playwright E2E. Cache dependencies by lockfile and upload only final test reports on failure.

- [ ] **Step 4: Execute the completion audit**

Run: `make lint && make test && make build && make e2e`  
Expected: all commands succeed from a clean checkout with no untracked generated files.

- [ ] **Step 5: Perform visual fidelity review**

Capture the final 1440×1000 scenario comparison, inspect it beside the accepted concept with `view_image`, and correct every material mismatch in typography, layout, color, chart anatomy, spacing, responsive behavior and interaction state.

- [ ] **Step 6: Commit**

```bash
git add README.md docs Makefile .env.example .github backend/Dockerfile frontend/Dockerfile frontend/e2e
git commit -m "chore: prepare OpenEnterprise Twin 0.1 release"
```

## Post-0.1 roadmap

### 0.2 — Policy Studio

- Add deterministic candidate screening and Monte Carlo finalist evaluation.
- Add efficient-frontier comparison, tornado sensitivity and driver decomposition.
- Add OR-Tools mixed-integer adapter for capacity and sourcing decisions.
- Add plugin discovery through `openenterprise_twin.plugins` entry points.
- Add PDF generation from the immutable report route.

### 0.3 — Enterprise Extension

- Replace in-process execution with a PostgreSQL-backed worker adapter while preserving lifecycle contracts.
- Add OIDC authentication, project roles and immutable decision audit records.
- Add S3-compatible artifacts and signed report snapshots.
- Add company-model import schema, reconciliation and data-quality diagnostics.
- Add versioned scenario collaboration and approval workflow.

### 1.0 — Trusted Decision Platform

- Publish a stable plugin SDK and compatibility policy.
- Add backtesting and calibration workbenches for every stochastic model.
- Add portfolio-level resource allocation across business units.
- Add event-driven observation ingestion and current-state projections.
- Establish benchmark datasets, performance targets and long-term support policy.
