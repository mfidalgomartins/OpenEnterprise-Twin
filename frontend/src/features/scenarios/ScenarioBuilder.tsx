import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { formatCurrency, formatDate } from "../../lib/format";
import { ExperimentProgress } from "./ExperimentProgress";
import { PolicyLever } from "./PolicyLever";
import { getBaselineScenario, getCompanyReference } from "./api";
import {
  buildCandidateScenario,
  changedDriverCount,
  defaultScenarioDraft,
  type ScenarioDraft,
  type ScenarioDraftField,
  validateScenarioDraft,
} from "./scenarioDraft";
import { useScenarioExperiment } from "./useScenarioExperiment";
import "./scenario-builder.css";

const DRAFT_STORAGE_KEY = "openenterprise-twin:scenario-draft:v1";

interface StoredDraft {
  draft: ScenarioDraft;
  saved_at: string;
  version: 1;
}

function loadStoredDraft(): StoredDraft {
  const fallback: StoredDraft = {
    draft: defaultScenarioDraft,
    saved_at: new Date().toISOString(),
    version: 1,
  };
  try {
    const stored = sessionStorage.getItem(DRAFT_STORAGE_KEY);
    if (!stored) {
      return fallback;
    }
    const parsed = JSON.parse(stored) as Partial<StoredDraft>;
    if (parsed.version !== 1 || !parsed.draft) {
      return fallback;
    }
    const draft = { ...defaultScenarioDraft, ...parsed.draft };
    if (Object.values(draft).some((value) => typeof value !== "string")) {
      return fallback;
    }
    return {
      draft,
      saved_at: parsed.saved_at ?? fallback.saved_at,
      version: 1,
    };
  } catch {
    return fallback;
  }
}

export function ScenarioBuilder() {
  const [initialDraft] = useState(loadStoredDraft);
  const [draft, setDraft] = useState(initialDraft.draft);
  const [savedAt, setSavedAt] = useState(initialDraft.saved_at);
  const baselineQuery = useQuery({
    queryFn: getBaselineScenario,
    queryKey: ["baseline-scenario"],
  });
  const companyQuery = useQuery({
    queryFn: getCompanyReference,
    queryKey: ["company-reference"],
  });
  const { isRunning, issue, lastCompleted, phase, runScenario } =
    useScenarioExperiment();

  useEffect(() => {
    const stored: StoredDraft = { draft, saved_at: savedAt, version: 1 };
    sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(stored));
  }, [draft, savedAt]);

  if (baselineQuery.isPending || companyQuery.isPending) {
    return (
      <section className="scenario-builder-state">
        <h1>Preparing policy studio</h1>
        <p>Loading the versioned baseline and addressable company drivers.</p>
      </section>
    );
  }

  if (
    baselineQuery.error ||
    companyQuery.error ||
    !baselineQuery.data ||
    !companyQuery.data
  ) {
    return (
      <section className="scenario-builder-state">
        <h1>Policy studio unavailable</h1>
        <p role="alert">
          The baseline or company model could not be loaded. Check the API and
          retry without clearing the saved draft.
        </p>
      </section>
    );
  }

  const baseline = baselineQuery.data;
  const company = companyQuery.data;
  const errors = validateScenarioDraft(draft, company);
  const changedCount = changedDriverCount(draft);
  const hasErrors = Object.keys(errors).length > 0;
  const testResource = company.plant.resources.find(
    (resource) => resource.resource_id === "test",
  );
  const electronics = company.plant.materials.find(
    (material) => material.material_id === "electronics",
  );
  const intelligentValve = company.products.find(
    (product) => product.product_id === "intelligent-valve",
  );
  const contracted = company.customer_segments.find(
    (segment) => segment.segment_id === "contracted",
  );

  function updateField(field: ScenarioDraftField, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
    setSavedAt(new Date().toISOString());
  }

  function submitScenario(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hasErrors || changedCount === 0 || isRunning) {
      return;
    }
    const candidate = buildCandidateScenario(draft, baseline);
    void runScenario({
      baseline,
      candidate,
      iterations: Number(draft.iterations),
      seed: Number(draft.seed),
    });
  }

  return (
    <article className="scenario-builder">
      <header className="scenario-builder__header">
        <div>
          <h1>Policy studio</h1>
          <p>
            Branch the current plan into an immutable, paired experiment. Each
            lever names the direct mechanism represented in the twin.
          </p>
        </div>
        <dl>
          <div>
            <dt>Baseline</dt>
            <dd>{baseline.name}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{company.model_version}</dd>
          </div>
          <div>
            <dt>Draft</dt>
            <dd>Saved {formatDate(savedAt)}</dd>
          </div>
        </dl>
      </header>

      <div className="scenario-builder__layout">
        <form className="scenario-builder__form" onSubmit={submitScenario}>
          <div className="scenario-builder__identity">
            <label htmlFor="scenario-name">Scenario name</label>
            <input
              aria-describedby={errors.name ? "scenario-name-error" : undefined}
              aria-invalid={Boolean(errors.name)}
              id="scenario-name"
              maxLength={160}
              onChange={(event) => updateField("name", event.target.value)}
              value={draft.name}
            />
            {errors.name ? (
              <p id="scenario-name-error" role="alert">
                {errors.name}
              </p>
            ) : null}
          </div>

          <fieldset className="scenario-builder__group">
            <legend>Commercial</legend>
            <p>Change demand economics before orders enter the operating model.</p>
            <PolicyLever
              baseline={`0% change (${formatCurrency((intelligentValve?.standard_price_cents ?? 0) / 100, { maximumFractionDigits: 0 })} standard)`}
              error={errors.price_change_percent}
              id="price-change"
              label="Spot intelligent valve price change"
              maximum={1_000}
              mechanism="Changes realized unit revenue and demand through the spot-segment price elasticity."
              minimum={-99.99}
              onChange={(value) => updateField("price_change_percent", value)}
              step="any"
              unit="%"
              value={draft.price_change_percent}
            />
            <PolicyLever
              baseline="0% change"
              error={errors.commercial_investment_percent}
              id="commercial-investment"
              label="Commercial investment change"
              maximum={1_000}
              mechanism="Changes demand through the configured segment-level commercial sensitivity."
              minimum={-100}
              onChange={(value) =>
                updateField("commercial_investment_percent", value)
              }
              step={0.1}
              unit="%"
              value={draft.commercial_investment_percent}
            />
          </fieldset>

          <fieldset className="scenario-builder__group">
            <legend>Operations</legend>
            <p>Change the finite test-cell minutes available to production.</p>
            <PolicyLever
              baseline={`${testResource?.daily_capacity_minutes ?? 0} min/day`}
              error={errors.capacity_change_percent}
              id="capacity-change"
              label="Test capacity change"
              maximum={1_000}
              mechanism="Changes regular test-cell capacity before jobs compete for constrained minutes."
              minimum={-100}
              onChange={(value) => updateField("capacity_change_percent", value)}
              step={0.1}
              unit="%"
              value={draft.capacity_change_percent}
            />
            <PolicyLever
              baseline={`0 min/day; ${testResource?.max_overtime_minutes ?? 0} max`}
              error={errors.overtime_minutes}
              id="overtime-minutes"
              label="Test overtime capacity"
              maximum={testResource?.max_overtime_minutes ?? 0}
              mechanism="Adds costed test minutes after regular capacity is exhausted."
              minimum={0}
              onChange={(value) => updateField("overtime_minutes", value)}
              unit="min/day"
              value={draft.overtime_minutes}
            />
          </fieldset>

          <fieldset className="scenario-builder__group">
            <legend>Supply</legend>
            <p>Change electronics availability and supplier economics.</p>
            <PolicyLever
              baseline={`0 days; supplier lead time ${electronics?.supplier_lead_time_days ?? 0} days`}
              error={errors.safety_stock_days}
              id="safety-stock"
              label="Electronics safety stock"
              maximum={365}
              mechanism="Raises the electronics reorder target using demand coverage days."
              minimum={0}
              onChange={(value) => updateField("safety_stock_days", value)}
              step={0.5}
              unit="days"
              value={draft.safety_stock_days}
            />
            <PolicyLever
              baseline="0% improvement"
              error={errors.supplier_lead_time_improvement_percent}
              id="supplier-lead-time"
              label="Supplier lead-time improvement"
              maximum={100}
              mechanism="Shortens stochastic replenishment lead time before material receipts arrive."
              minimum={0}
              onChange={(value) =>
                updateField("supplier_lead_time_improvement_percent", value)
              }
              step={0.1}
              unit="%"
              value={draft.supplier_lead_time_improvement_percent}
            />
            <PolicyLever
              baseline="0% change"
              error={errors.supplier_cost_change_percent}
              id="supplier-cost"
              label="Electronics supplier cost change"
              maximum={1_000}
              mechanism="Changes purchase cost for every electronics module received."
              minimum={-99.99}
              onChange={(value) =>
                updateField("supplier_cost_change_percent", value)
              }
              step="any"
              unit="%"
              value={draft.supplier_cost_change_percent}
            />
          </fieldset>

          <fieldset className="scenario-builder__group">
            <legend>Finance</legend>
            <p>Change working-capital timing and one-off investment cash.</p>
            <PolicyLever
              baseline={`${contracted?.payment_terms_days ?? 0} days`}
              error={errors.payment_terms_change_days}
              id="payment-terms"
              label="Contracted payment-term change"
              maximum={365 - (contracted?.payment_terms_days ?? 0)}
              mechanism="Moves customer cash collection relative to the contracted baseline."
              minimum={-(contracted?.payment_terms_days ?? 0)}
              onChange={(value) =>
                updateField("payment_terms_change_days", value)
              }
              unit="days"
              value={draft.payment_terms_change_days}
            />
            <PolicyLever
              baseline="€0"
              error={errors.capital_investment_eur}
              id="capital-investment"
              label="One-off capital investment"
              mechanism="Reduces cash once at scenario start and remains explicit in the value bridge."
              minimum={0}
              onChange={(value) => updateField("capital_investment_eur", value)}
              step={100}
              unit="EUR"
              value={draft.capital_investment_eur}
            />
          </fieldset>

          <fieldset className="scenario-builder__execution">
            <legend>Experiment settings</legend>
            <div>
              <label htmlFor="iterations">Paired iterations</label>
              <input
                aria-invalid={Boolean(errors.iterations)}
                id="iterations"
                max={1_000}
                min={1}
                onChange={(event) =>
                  updateField("iterations", event.target.value)
                }
                type="number"
                value={draft.iterations}
              />
              {errors.iterations ? <p role="alert">{errors.iterations}</p> : null}
            </div>
            <div>
              <label htmlFor="seed">Random seed</label>
              <input
                aria-invalid={Boolean(errors.seed)}
                id="seed"
                min={0}
                onChange={(event) => updateField("seed", event.target.value)}
                type="number"
                value={draft.seed}
              />
              {errors.seed ? <p role="alert">{errors.seed}</p> : null}
            </div>
          </fieldset>

          <div className="scenario-builder__submit">
            <p>
              <strong>
                {changedCount} changed {changedCount === 1 ? "driver" : "drivers"}
              </strong>
              <span>
                The baseline and candidate use the same random shock tape.
              </span>
            </p>
            <button
              disabled={hasErrors || changedCount === 0 || isRunning}
              type="submit"
            >
              {isRunning ? "Recalculating…" : "Run comparison"}
            </button>
          </div>
        </form>

        <aside className="scenario-builder__rail">
          <ExperimentProgress
            issue={issue}
            lastCompleted={lastCompleted}
            phase={phase}
          />
        </aside>
      </div>
    </article>
  );
}
