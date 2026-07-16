# OpenEnterprise Twin — Product and Architecture Design

**Status:** Approved for implementation  
**Date:** 2026-07-16  
**Product category:** Enterprise decision intelligence and digital twin  
**Licence:** Apache-2.0

## Product thesis

OpenEnterprise Twin models a company as an interconnected dynamic system rather than a collection of dashboards. It lets decision-makers change a policy, simulate how the decision propagates through demand, operations, inventory, service, profit and cash, and compare the resulting distribution of outcomes with a baseline.

The initial product is intentionally opinionated: it models a mid-market B2B industrial company with contracted and transactional revenue, finite production capacity, component inventory, supplier lead times and working-capital constraints. This vertical slice is broad enough to demonstrate cross-functional effects and narrow enough to remain explainable, testable and credible.

The product does not claim to predict the future. Every result is conditional on explicit assumptions, model versions and random seeds. The primary output is a decision brief: expected value, downside exposure, binding constraints, trade-offs and the conditions under which a policy should be adopted.

## Design principles

1. **Decision-first:** every screen and API resource starts with a decision, not a metric.
2. **Uncertainty is visible:** distributions, percentiles and probability of breach take precedence over single-point forecasts.
3. **Assumptions are inspectable:** model parameters, provenance and scenario changes are first-class data.
4. **Financial and operational coherence:** orders, production, service, profit and cash reconcile in every simulation run.
5. **Deterministic by default:** a scenario is reproducible from configuration, model version and seed.
6. **Modular, not microservice-heavy:** bounded modules in one deployable backend until scale justifies separation.
7. **Executive clarity:** conclusions and trade-offs lead; analytical detail remains available on demand.
8. **Open extension:** new demand, operations, finance and objective models can be registered without changing the engine.

## Core user journey

1. The user opens the **Decision Room** and sees the baseline outlook, three strategic tensions and the most material constraint.
2. The user creates a scenario by changing a small set of policy levers: price, demand investment, production capacity, safety stock, supplier terms or payment terms.
3. A deterministic preview checks feasibility and explains the direct causal chain.
4. The user runs a Monte Carlo experiment and watches progress without blocking the interface.
5. The result compares baseline and policy across value, service, resilience and liquidity.
6. The system generates an executive recommendation with evidence, downside conditions and model limitations.
7. The scenario can be exported as a signed, reproducible decision brief.

## Demonstration company

The included demo company, **Northstar Components**, produces two industrial components for three customer segments across contracted and spot channels.

### Commercial system

- Segments: strategic accounts, core accounts and spot buyers.
- Products: precision module and control assembly.
- Demand depends on baseline volume, seasonality, price elasticity, commercial investment and service reputation.
- Contracted customers have lower elasticity and churn; spot buyers have higher elasticity and order volatility.
- Lost service reduces next-period demand through a bounded reputation effect.

### Operating system

- One plant with two work centres and finite effective hours.
- Product-specific cycle times and contribution margins.
- Shared component inventory with stochastic supplier lead time.
- Unfulfilled demand becomes backlog subject to cancellation.
- Overtime adds capacity at a premium and is capped.

### Financial system

- Revenue is recognized on shipment.
- Variable cost is recognized with production; fixed cost by period.
- Receivables and payables follow configurable terms.
- Inventory is valued at standard cost.
- Cash reconciles from opening cash, collections, supplier payments, payroll, fixed costs, overtime and capital investment.

## Simulation model

The simulation advances in monthly periods. Within each period, modules execute in a fixed order:

1. update exogenous conditions and seasonality;
2. generate demand by segment and product;
3. convert demand to orders and add backlog;
4. receive inbound supply;
5. allocate materials and effective capacity;
6. produce and ship orders using priority rules;
7. calculate service, cancellations and reputation;
8. recognize revenue, cost and working-capital movements;
9. settle receivables and payables;
10. update the immutable period snapshot.

The engine produces one `SimulationTrace` per iteration and an aggregated `ExperimentResult` across iterations. Each trace must pass accounting and flow-conservation checks before aggregation.

### Stochastic elements

- Demand shocks: correlated log-normal shocks by segment.
- Supplier lead time: discrete bounded distribution.
- Yield: beta distribution bounded by product-specific quality floors.
- Cancellation: binomial draw conditional on backlog age.
- Collections delay: discrete draw around contractual payment terms.

Randomness is provided through a seeded NumPy generator passed explicitly to each model. Modules must never use global random state.

### Primary outcomes

- Revenue, EBITDA and free cash flow.
- Closing cash and probability of a liquidity floor breach.
- Gross margin and return on invested working capital.
- On-time-in-full service level and backlog.
- Capacity utilization, overtime and material availability.
- Customer retention proxy and lost demand.

For each metric, the result includes mean, median, P10, P90, standard deviation and probability of crossing its configured guardrail.

## Scenario semantics

A scenario contains metadata, a baseline reference and a set of validated policy changes. Policy changes are expressed as typed values rather than arbitrary dictionaries. Initial levers are:

- price change by segment and product;
- commercial investment change;
- regular capacity change;
- overtime ceiling;
- safety-stock coverage;
- supplier lead-time improvement and unit-cost trade-off;
- customer payment-term change;
- one-off capital investment.

Every experiment stores the complete resolved assumptions, engine version, schema version, seed, iteration count and runtime. Two scenarios can be compared only when their company model and horizon are compatible.

## Optimization

Optimization is a separate application service over the simulation engine. The first optimizer uses bounded search over policy levers and a deterministic approximation to identify candidates, then evaluates finalists with Monte Carlo.

The objective is a weighted risk-adjusted score:

`expected_free_cash_flow - downside_penalty - service_breach_penalty - liquidity_breach_penalty`

Hard constraints may include minimum service, minimum closing cash, maximum overtime and maximum capital investment. A later OR-Tools adapter supports mixed-integer capacity and sourcing choices. Optimizer output always contains a feasible-set explanation; it never returns a recommendation without showing binding constraints.

## Architecture

The repository is a monorepo with three deployable surfaces:

```text
React decision cockpit
        │ typed HTTP
FastAPI application
        │ application services
Domain + simulation + optimization kernel
        │ repositories
PostgreSQL transactional store
```

### Backend boundaries

- `domain`: immutable entities, value objects, invariants and result types.
- `simulation`: deterministic orchestration and pluggable model implementations.
- `scenarios`: scenario validation, comparison and experiment lifecycle.
- `optimization`: objective definitions and policy search.
- `reporting`: executive narrative and export-ready report model.
- `plugins`: typed protocols, capability metadata and registry.
- `infrastructure`: persistence, settings, logging and runtime adapters.
- `api`: transport schemas, dependency wiring, routes and error mapping.

Domain and simulation code cannot import FastAPI, SQLAlchemy or frontend contracts. Infrastructure implements repository protocols defined by the application boundary.

### Frontend boundaries

- `app`: shell, routing and query client.
- `features/decision-room`: baseline and strategic overview.
- `features/scenarios`: scenario creation and experiment progress.
- `features/analysis`: distribution, driver and constraint analysis.
- `features/reports`: executive brief composition and export.
- `components`: shared data-display and interaction primitives.
- `lib`: API client, formatting, chart configuration and tokens.

The frontend uses React, TypeScript, Vite, TanStack Query, React Router, Recharts and Lucide icons. The first release has no client-side global state library; server state belongs to Query and local interaction state remains in feature components. The executive navigation is horizontal to preserve analytical canvas width. Inside a scenario, the information architecture is `Overview · Assumptions · Outcomes · Sensitivities · Evidence`.

### API resources

- `GET /api/v1/health`
- `GET /api/v1/company`
- `GET /api/v1/baseline`
- `GET /api/v1/scenarios`
- `POST /api/v1/scenarios`
- `GET /api/v1/scenarios/{scenario_id}`
- `POST /api/v1/scenarios/{scenario_id}/experiments`
- `GET /api/v1/experiments/{experiment_id}`
- `GET /api/v1/experiments/{experiment_id}/comparison`
- `GET /api/v1/experiments/{experiment_id}/report`

Initial experiments execute in-process through a bounded worker pool with explicit lifecycle states. The interface isolates execution so Redis/Celery or a managed queue can be introduced without changing route contracts.

## Plugin architecture

Plugins are Python entry points grouped under `openenterprise_twin.plugins`. A plugin declares an identifier, semantic version, compatible engine range, configuration schema and one or more capabilities.

Initial capability protocols are:

- `DemandModel`
- `OperationsModel`
- `FinanceModel`
- `RiskMetric`
- `OptimizationStrategy`
- `ReportSection`

Plugins receive immutable inputs and return typed outputs. Registration rejects duplicate capability IDs, incompatible versions and invalid configuration at startup. Third-party plugin failures are isolated at the experiment boundary and reported with a stable error code.

## Persistence and provenance

PostgreSQL is the only supported relational datastore through SQLAlchemy 2.0 and is started locally through Docker Compose. This avoids dual-backend semantics while retaining a one-command developer experience. Persisted records include companies, scenarios, experiments, metric summaries and reports. Full simulation traces are stored as compressed JSON artifacts through an artifact-store interface and are not placed in transactional tables.

Every result exposes:

- company-model version;
- scenario schema version;
- engine version;
- plugin versions;
- random seed;
- iteration count;
- creation time and duration;
- resolved assumptions hash.

## Executive experience

The visual direction is restrained editorial enterprise software: a bright boardroom working surface, graphite documentation, mineral neutrals and one moss-green decision accent. Inter Variable provides both interface typography and tabular numerals. Rounded containers are used sparingly; hierarchy comes from spacing, typography, rules and contrast rather than card grids. The product avoids ornamental dark mode, glass effects, gradients and generic navy-and-gold finance styling.

The primary screen is a horizontal decision narrative:

1. **Current posture:** one concise conclusion and four outcome signals.
2. **System map:** demand, operations, service and cash connected as a causal flow.
3. **Strategic tensions:** three trade-off plots, not decorative KPI cards.
4. **Scenario ledger:** saved decisions with status, uncertainty and material impact.
5. **Decision brief:** recommendation, downside trigger and next action.

Charts prioritize distributions, ranges and deltas: fan charts, percentile bands, slope charts, waterfall charts, constraint bars, efficient-frontier plots, tornado charts and small multiples. Titles state the conclusion rather than the chart type. Pie charts, gauges, dual axes, traffic-light overload and generic gradient cards are excluded.

## Reporting

The report service creates a transport-neutral `ExecutiveBrief` consumed by HTML and PDF renderers. A brief contains:

- recommendation and decision status;
- quantified value and risk;
- operating mechanism explaining the result;
- outcome distribution;
- binding constraints;
- guardrail breaches;
- sensitivity and downside conditions;
- assumptions and reproducibility record.

Narrative text is deterministic and template-driven in the first release. It must reference computed evidence and avoid unsupported claims. Optional language-model adapters may be added later, but never sit on the critical path or replace the evidence model.

## Reliability, security and observability

- Pydantic validates all external configuration and API payloads.
- Domain invariants reject negative physical quantities, impossible rates and incoherent calendars.
- Experiments have execution budgets for iterations, horizon and runtime.
- API errors use stable codes and correlation IDs without exposing stack traces.
- Structured logs include request, scenario and experiment identifiers.
- Health endpoints separate liveness and readiness in production configuration.
- CORS is deny-by-default outside development.
- Report output escapes user-provided labels.
- Dependencies are pinned through lockfiles and scanned in CI.

## Testing strategy

- Unit tests for value objects, equations and period transitions.
- Property tests for non-negativity, flow conservation and accounting reconciliation.
- Determinism tests proving identical seed and inputs produce identical results.
- Statistical tests on stochastic generators using tolerances rather than exact samples.
- API contract tests for success, validation and lifecycle errors.
- Frontend component tests for scenario interactions and narrative states.
- End-to-end test from scenario creation to report generation.
- Performance benchmark for 1,000 iterations over 24 periods.

## Release scope

### Release 0.1 — Decision Room

- Reproducible Northstar company model.
- Baseline and scenario simulation.
- Monte Carlo aggregation and risk metrics.
- FastAPI scenario and experiment API.
- Premium React decision room and scenario builder.
- Deterministic executive brief.
- PostgreSQL persistence, Docker and CI.

### Release 0.2 — Policy Studio

- Candidate search and risk-adjusted optimization.
- Sensitivity analysis and driver decomposition.
- PostgreSQL deployment profile.
- PDF report rendering.
- Plugin discovery through Python entry points.

### Release 0.3 — Enterprise Extension

- OR-Tools capacity and sourcing adapter.
- Background queue adapter and horizontal workers.
- Role-based access adapter and audit trail.
- Company-model import schema and data-quality diagnostics.
- Versioned scenario collaboration.

## Acceptance criteria for 0.1

1. A clean checkout runs locally with one documented command for backend and frontend.
2. The included baseline is reproducible and all accounting/flow invariants pass.
3. A user can create a scenario, run at least 1,000 iterations and compare it with baseline.
4. The result includes distributions, guardrail probabilities and an evidence-backed recommendation.
5. API and UI expose complete provenance for every experiment.
6. Unit, integration, frontend and end-to-end tests pass in CI.
7. The primary desktop experience and responsive layout meet the approved visual concept.
8. Documentation explains architecture, model assumptions, extension points and limitations.
9. The repository contains no placeholders, generated filler copy, dead modules or duplicate implementations.

## Explicit exclusions from 0.1

- ERP/CRM connectors.
- Multi-tenant authentication and billing.
- Real-time streaming.
- Autonomous execution of business decisions.
- Arbitrary user-authored simulation code in the hosted process.
- Claims of causal identification from synthetic data.

These exclusions protect the credibility of the initial release while preserving interfaces for later enterprise adapters.
