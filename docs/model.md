# Model reference

This document describes the equations and validation rules implemented by the 0.2 Northstar simulation. It is a code-level contract, not a claim that the parameters are calibrated to a real manufacturer.

## Scope and clock

The engine is a daily hybrid state-transition and order-event simulation starting on `2025-01-01`. Weekdays are operating days; orders and production starts are suppressed on weekends, while receipts, completions, collections, payments, financing and ledger close still follow the daily clock.

The standard 515-day lifecycle is:

| Phase | Days | Measurement treatment |
| --- | ---: | --- |
| Warm-up | 91 | Evolves state; excluded from evaluation metrics |
| Evaluation | 364 | Creates measured demand and accumulates operating/financial metrics |
| Runoff | 60 | Creates no new demand; resolves evaluation-period orders where possible |

For shorter test scenarios, the complete horizon is evaluation-only. Every day executes in this order: supplier receipts; work-order completions and yield; collections and supplier payments; liquidity financing; demand creation; earliest-due shipment allocation; overdue cancellation; finite-capacity production planning; material procurement; operating costs, interest and final financing; retention update; immutable ledger validation.

## Units

| Quantity | Stored unit | Notes |
| --- | --- | --- |
| Money | integer euro cents | Prices, costs, cash, debt and capital investment |
| Material cost | integer milli-cents per base unit | Converted to cents with integer division when production cost is derived |
| Product flow | integer units | Orders, finished goods, WIP, shipments and backlog |
| Steel | integer grams | Northstar's `steel` material base unit |
| Electronics | integer modules | Northstar's `electronics` material base unit |
| Capacity | integer minutes | Resource availability, use and overtime |
| Rates and policy changes | decimal fractions | `0.05` means 5%; probabilities are in `[0,1]` |
| Time | integer calendar-day index | Dates are derived from the fixed start date |
| Service metrics | ratio in `[0,1]` | OTIF and cancellation rate |

Money never uses binary floating point inside a daily ledger. Decimal policy arithmetic is rounded at declared boundaries; state and reconciliation fields remain integers.

## Demand and orders

For product $p$, segment $s$ and day $t$, conditional expected units are:

$$
\mu_{p,s,t} = B_{p,s}
\times (1+\Delta P_{p,s})^{\epsilon_{p,s}}
\times M_{p,t}
\times S_{p,s,t}
\times (1+\Delta C\,\gamma_{p,s})
\times R_{s,t}.
$$

`B` is daily baseline units, `ΔP` is the relative price change, `ε < 0` is price elasticity, `M` is the stochastic market/product multiplier, `ΔC` is commercial-investment change, `γ` its sensitivity and `R` the segment retention factor. The seasonal factor is:

$$
S_{p,s,t}=1+a_{p,s}\sin\left(2\pi\frac{t\bmod365}{365}\right).
$$

Order count follows an NB2 negative binomial. With mean order size $q_s$, count mean $m=\mu/q_s$ and dispersion $k_s$:

$$
N_{p,s,t}\sim\operatorname{NegBin}(m,k_s),\qquad
\operatorname{Var}(N)=m+\frac{m^2}{k_s}.
$$

The tape provides one uniform draw and the engine applies the inverse CDF. Ordered units are `N × mean_order_size`; all orders in that product/segment/day group share one stable order record. Net unit price is rounded to cents:

$$
P^{net}_{p,s}=\operatorname{round}\left(P^{standard}_p(1-d_s)(1+\Delta P_{p,s})\right).
$$

Commercial demand lift is not free. On operating days in warm-up and evaluation, the signed incremental expense is the configured daily commercial-investment baseline multiplied by $\Delta C$. A negative change reduces both demand support and spend.

Shipments allocate finished goods by earliest due date and then stable order ID. Revenue and standard COGS are recognized on shipment. Receivables become due on `shipment day + effective payment terms + collection delay`, never earlier than the following day because collections have already executed in the daily chronology.

Overdue open orders are eligible after promised lead time plus grace. Their cancelled order count is a binomial inverse-CDF draw with segment cancellation probability. Unit cancellation reconciles partial and untouched mean-size orders.

## Retention

For each segment, annual contractual retention is converted to a daily factor. Late share is the ratio of late open units to all open units:

$$
R_{s,t+1}=\operatorname{clip}_{[0,1]}\left(
R_{s,t}(1-c_s)^{1/365}-\frac{\lambda_s\,L_{s,t}}{30}
\right),
$$

where `c` is annual churn probability, `λ` service-reputation sensitivity and `L` late share. Lost demand records the rounded gap between demand before and after retention.

## Capacity, production and material

Stochastic regular capacity for resource $r$ is floored to integer minutes:

$$
C^{regular}_{r,t}=\left\lfloor
C^{base}_r(1+\Delta C_r)Z^{capacity}_{r,t}
\right\rfloor,
\qquad
C^{total}_{r,t}=C^{regular}_{r,t}+C^{overtime}_r.
$$

Products are planned by descending backlog, then product ID. Finished-goods target covers production lead time plus the longest customer promise:

$$
T_p=(L^{production}_p+\max_s L^{promise}_s)\sum_s B_{p,s}.
$$

Requested starts are:

$$
Q^{need}_{p,t}=\max(0,T_p+Backlog_{p,t}-FG_{p,t}-WIP_{p,t}).
$$

Actual starts are the minimum of need and every integer resource/material bound. Capacity and materials are consumed immediately; work completes after the configured number of operating days. Good output is `round(completed starts × stochastic yield)` and the remainder is scrap.

For material $m$, baseline daily use is the sum of baseline product demand times bill-of-material units. A policy adds safety stock:

$$
SS_m=\left\lceil coverage_m\times daily\_use_m\right\rceil.
$$

The effective reorder point and order-up-to level are baseline levels plus `SS`. Procurement triggers when on-hand plus open purchase orders is at or below the reorder point. Contractual lead time is multiplied by `1 - lead_time_improvement`, rounded and bounded to at least one day; the stochastic supplier delay is then added.

Supplier unit-cost changes modify the material contribution to effective product cost and therefore shipment COGS and scrap expense. Regular capacity changes carry a daily commitment cost equal to signed incremental minutes times the resource's configured capacity cost; overtime remains charged only when used.

## Finance and liquidity

Daily fixed cost uses integer cents:

$$
Fixed_t=\left\lfloor\frac{MonthlyFixed\times12}{365}\right\rfloor.
$$

Daily interest is rounded half-up:

$$
Interest_t=\operatorname{round}\left(Debt^{opening}_t\frac{r_{annual}}{365}\right).
$$

Conversion cash cost per production start is standard unit cost less standard material cost. Supplier cash cost is paid separately through payables. The exact daily cash identity is:

$$
Cash^{close}=Cash^{open}+Collections+Rescue+RevolverDraw
-SupplierPayments-ConversionCost-OvertimeCost-FixedCost
-CommercialInvestmentChange-CapacityCommitmentChange
-Interest-CapitalInvestment-RevolverRepayment.
$$

Financing first draws the revolver up to its limit to restore the liquidity floor. In experiment mode, any remaining shortfall is recorded as rescue funding so the trace can complete and the breach can be quantified. Excess cash above the target repays outstanding debt after daily operating costs. Debt reconciles as:

$$
Debt^{close}=Debt^{open}+RevolverDraw-RevolverRepayment.
$$

## Stochastic tape

All random values are generated before business transitions by NumPy Philox. A generator key is derived with SHA-256 from:

```text
tape version | master seed | replication | process | day | entity | draw ID
```

Separate SHA-256 domains derive Philox counter and key. This makes draws stable under iteration ordering and lets baseline and candidate use common random numbers without shifting unrelated streams.

| Process | Implemented assumption |
| --- | --- |
| Market demand state | Normalized AR(1), `ρ = 0.65` |
| Product demand state | Normalized AR(1), `ρ = 0.35` |
| Demand multiplier | `exp(0.08 × market + 0.12 × product - 0.5 × 0.0208)`; mean-one under stationary normal states |
| Order arrivals | NB2 inverse CDF with segment dispersion |
| Capacity availability | Lognormal `exp(0.03z - 0.5 × 0.03²)` |
| Yield | Product-floor-shifted beta, concentration `160`, mean equal to baseline yield before rounding |
| Supplier delay | `Binomial(7, 0.25)` additional calendar days |
| Cancellation | Binomial over eligible open order count |
| Collection delay | `-3` days (10%), `0` (55%), `+2` (20%), `+7` (10%), `+14` (5%) |

A normalized AR(1) transition is:

$$
x_t=\rho x_{t-1}+\sqrt{1-\rho^2}\,\varepsilon_t,
\qquad \varepsilon_t\sim\mathcal{N}(0,1).
$$

The product quality floor is `max(0, baseline_yield - min(0.08, baseline_yield/2))`. Supplier and collection distributions are bounded. None of these reference values has been calibrated against an external dataset.

## Experiment metrics

Metrics use evaluation days unless stated otherwise:

| Metric | Definition and unit |
| --- | --- |
| Revenue | Sum of shipment revenue, cents |
| EBITDA | Evaluation revenue − effective shipment COGS − scrap expense − fixed cost − overtime − commercial-investment change − capacity-commitment change; cents |
| Free cash flow | Evaluation-origin collections through runoff plus residual evaluation receivables, less evaluation-origin supplier payments through runoff, residual evaluation payables, evaluation conversion/overtime/fixed/interest/commercial/capacity costs and full-trace capital investment; cents |
| Closing cash | Final runoff-day cash, cents |
| OTIF | Evaluation-origin orders fulfilled entirely on time ÷ evaluation orders |
| Cancellation rate | Cancelled evaluation-origin orders ÷ evaluation orders |
| Backlog units | Open units at final trace close |
| Capacity utilization | Evaluation used minutes ÷ available minutes across resources |
| Peak revolver | Maximum daily closing revolver debt, cents |
| Rescue funding | Sum of synthetic liquidity support over the full trace, cents |

For each metric, NumPy's deterministic linear percentile estimator produces P5, P10, median, P90 and P95. Standard deviation is population standard deviation (`ddof=0`). Breaches use strict `<` or `>` comparisons and report bounded 95% Wilson score intervals. Empirical CVaR integrates exactly 5% of probability mass, fractionally weighting the boundary observation when necessary. Closing-cash breach probability also counts any replication that required rescue funding.

Scenario comparison aligns replication IDs and shock-tape digests, then computes candidate minus baseline for every metric. It reports paired mean difference, a 95% Student-t interval when supported by sample size (using conservative tabulated critical values), paired P5/P50/P95 and probability of improvement under the company-owned direction and materiality threshold. This is variance reduction, not causal identification.

## Invariants

Pydantic rejects negative ledger quantities before a period is accepted. The simulation validates dimensions and then raises `InvariantViolation` with a stable code when any of these laws fails:

| Code | Required identity or bound |
| --- | --- |
| `dimension_mismatch` | Every daily mapping uses the complete declared product, material or resource dimension |
| `finished_goods_conservation` | Opening FG + good production = shipments + closing FG |
| `wip_conservation` | Opening WIP + starts = completions + closing WIP |
| `production_yield_conservation` | Good production + scrap = completed starts |
| `backlog_conservation` | Opening backlog + new orders = shipments + cancellations + closing backlog |
| `material_conservation` | Opening material + receipts = consumption + closing material |
| `capacity_limit` | Used minutes ≤ available minutes |
| `overtime_bound` | Overtime minutes ≤ used minutes |
| `otif_bound` | OTIF order count ≤ fulfilled order count |
| `evaluation_order_bound` | Evaluation outcomes cannot exceed all-period outcomes |
| `on_time_shipment_bound` | On-time shipped units ≤ shipped units |
| `cash_reconciliation` | Daily cash identity holds exactly to the cent |
| `debt_reconciliation` | Daily debt identity holds exactly |
| `state_continuity` | Every closing physical/financial state equals the next opening state |
| `evaluation_order_conservation` | Evaluation orders = fulfilled + cancelled + open after runoff |
| `period_sequence` | Period indexes are contiguous and start at zero |
| `empty_trace` | A trace contains at least one period |
| `trace_digest` | Canonical trace content matches its SHA-256 digest |

Production planning separately proves that declared starts reconcile to resource minutes and material consumption (`production_capacity_reconciliation`, `production_material_reconciliation`), remain within available bounds (`production_capacity_limit`, `production_material_limit`) and use the full product dimension (`production_dimension_mismatch`). Experiment checks cover contiguous replication IDs, metric and guardrail dimensions, distribution recomputation, lifecycle totals and plugin identities. Comparison and brief checks enforce paired evidence compatibility, content digests and brief-to-comparison evidence linkage.

## Reproducibility record

Every trace retains company-model version, scenario-schema version, engine version, tape version, RNG algorithm, seed, replication ID, resolved-assumption hash, tape digest and trace digest. Experiments add company hash, plugin versions, replication count, lifecycle, guardrails, timestamps and result digest. Comparisons retain both experiment and assumption digests; briefs retain their own schema version, the comparison digest and all source provenance.

Reproduction therefore requires the recorded code-compatible versions and the complete resolved company/scenario inputs—not only the seed.

## Decision-loop analytics (v0.3+)

The `analytics` layer is a pure, deterministic extension of the model. Its
mathematics are documented here; every artifact is content-addressed with a
SHA-256 over its canonical JSON (`analytics/_digest.canonical_digest`).

### Calibration

For each canonical series and entity, the sample mean is the point estimate and
the sample standard deviation is the dispersion. A parameter is `observed` when
the sample size is at least 30, `estimated` for 2–29 points, and `assumed` when
no history exists (carried over from the authored company model). The 95%
confidence interval of the mean uses the normal approximation
`x̄ ± z·s/√n` with `z = 1.959964`. Seasonality is a bucketed multiplicative
index (weekday or calendar month); the profile with the larger amplitude
`(max−min)/2` is retained as the dominant pattern.

### Backtesting

Calibration is fit on `[start, cutoff]` and validated on `(cutoff, end]` — a
strict temporal split, never random. Per observable it reports MAE, RMSE,
weighted MAPE (`Σ|error| / Σ|observed|`), bias and empirical interval coverage
against the prediction interval `x̄ ± z·s`. Expanding-window rolling backtests
run one calibration per cutoff.

### Credibility score

`score = 100 · Σ wᵢ · nᵢ` over seven components `nᵢ ∈ [0,1]` with fixed weights:
data quality `0.20`, temporal coverage `0.15`, backtest error `0.20`, interval
coverage `0.15`, parameter stability `0.10`, assumed ratio `0.10`, recent drift
`0.10`. Backtest error is `1 − min(1, wMAPE/τ)` with tolerance `τ = 0.30`.
Bands: `≥80` decision_grade, `≥60` supporting, `≥40` provisional, else
insufficient.

### Optimization

NSGA-II minimises the sign-adjusted objective vector under constraint
domination: a feasible candidate dominates any infeasible one, and among
infeasible candidates the lower total hard-constraint violation dominates.
Crowding distance breaks ties. SBX crossover (`η=15`) and polynomial mutation
(`η=20`) drive a seeded PCG64 generator, and an evaluation cache keyed by the
rounded genome respects the compute budget. The run is reproducible for a given
seed.

### Monitoring and drift

Per KPI, the adverse deviation is standardised by the predicted half-width. The
alert level escalates `within_expectation → early_warning → material_deviation
→ decision_review_required` at `0`, `1.5` and `3.0` adverse half-widths; a
breached hard constraint forces a review. Drift is the maximum of result drift
(mean standardised adverse deviation / 3), parameter drift (`|Δ|/0.5`) and data
drift; recalibration is recommended at severity `≥ 0.5`.

### CSV ingestion

Long-format CSV (`period_date, series, entity_id, value, unit`) is validated
row by row against the canonical series registry. Export neutralises leading
formula characters (`= + - @`) so a downloaded dataset cannot execute in a
spreadsheet.
