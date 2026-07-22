import type {
  CompanyReference,
  ScenarioPayload,
  ScenarioResource,
} from "./types";

export interface ScenarioDraft {
  name: string;
  price_change_percent: string;
  commercial_investment_percent: string;
  capacity_change_percent: string;
  overtime_minutes: string;
  safety_stock_days: string;
  supplier_lead_time_improvement_percent: string;
  supplier_cost_change_percent: string;
  payment_terms_change_days: string;
  capital_investment_eur: string;
  iterations: string;
  seed: string;
}

export type ScenarioDraftField = keyof ScenarioDraft;
export type ScenarioDraftErrors = Partial<Record<ScenarioDraftField, string>>;

export const defaultScenarioDraft: ScenarioDraft = {
  name: "Policy experiment",
  price_change_percent: "0",
  commercial_investment_percent: "0",
  capacity_change_percent: "0",
  overtime_minutes: "0",
  safety_stock_days: "0",
  supplier_lead_time_improvement_percent: "0",
  supplier_cost_change_percent: "0",
  payment_terms_change_days: "0",
  capital_investment_eur: "0",
  iterations: "100",
  seed: "731",
};

const leverFields: readonly ScenarioDraftField[] = [
  "price_change_percent",
  "commercial_investment_percent",
  "capacity_change_percent",
  "overtime_minutes",
  "safety_stock_days",
  "supplier_lead_time_improvement_percent",
  "supplier_cost_change_percent",
  "payment_terms_change_days",
  "capital_investment_eur",
];

function numericValue(value: string) {
  return value.trim() === "" ? Number.NaN : Number(value);
}

function errorForRange(
  value: string,
  minimum: number,
  maximum: number,
  message: string,
) {
  const number = numericValue(value);
  return Number.isFinite(number) && number >= minimum && number <= maximum
    ? undefined
    : message;
}

function errorForExclusiveMinimum(
  value: string,
  minimum: number,
  maximum: number,
  message: string,
) {
  const number = numericValue(value);
  return Number.isFinite(number) && number > minimum && number <= maximum
    ? undefined
    : message;
}

function integerError(
  value: string,
  minimum: number,
  maximum: number,
  message: string,
) {
  const number = numericValue(value);
  return Number.isInteger(number) && number >= minimum && number <= maximum
    ? undefined
    : message;
}

function findResource(company: CompanyReference, resourceId: string) {
  return company.plant.resources.find(
    (resource) => resource.resource_id === resourceId,
  );
}

function findSegment(company: CompanyReference, segmentId: string) {
  return company.customer_segments.find(
    (segment) => segment.segment_id === segmentId,
  );
}

export function validateScenarioDraft(
  draft: ScenarioDraft,
  company: CompanyReference,
): ScenarioDraftErrors {
  const errors: ScenarioDraftErrors = {};
  const testResource = findResource(company, "test");
  const contracted = findSegment(company, "contracted");

  if (draft.name.trim().length < 1 || draft.name.trim().length > 160) {
    errors.name = "Scenario name must contain between 1 and 160 characters.";
  }
  errors.price_change_percent = errorForExclusiveMinimum(
    draft.price_change_percent,
    -100,
    1_000,
    "Price change must be greater than -100% and no more than 1,000%.",
  );
  errors.commercial_investment_percent = errorForRange(
    draft.commercial_investment_percent,
    -100,
    1_000,
    "Commercial investment must be between -100% and 1,000%.",
  );
  errors.capacity_change_percent = errorForRange(
    draft.capacity_change_percent,
    -100,
    1_000,
    "Capacity change must be between -100% and 1,000%.",
  );
  errors.overtime_minutes = integerError(
    draft.overtime_minutes,
    0,
    testResource?.max_overtime_minutes ?? 0,
    `Overtime must be a whole number from 0 to ${testResource?.max_overtime_minutes ?? 0} minutes.`,
  );
  errors.safety_stock_days = errorForRange(
    draft.safety_stock_days,
    0,
    365,
    "Safety stock must be between 0 and 365 days.",
  );
  errors.supplier_lead_time_improvement_percent = errorForRange(
    draft.supplier_lead_time_improvement_percent,
    0,
    100,
    "Lead-time improvement must be between 0% and 100%.",
  );
  errors.supplier_cost_change_percent = errorForExclusiveMinimum(
    draft.supplier_cost_change_percent,
    -100,
    1_000,
    "Supplier cost change must be greater than -100% and no more than 1,000%.",
  );
  const paymentTerms = contracted?.payment_terms_days ?? 0;
  errors.payment_terms_change_days = integerError(
    draft.payment_terms_change_days,
    -paymentTerms,
    365 - paymentTerms,
    `Payment-term change must keep effective terms between 0 and 365 days (${paymentTerms} day baseline).`,
  );
  errors.capital_investment_eur = errorForRange(
    draft.capital_investment_eur,
    0,
    Number.MAX_SAFE_INTEGER / 100,
    "Capital investment must be a non-negative EUR amount.",
  );
  errors.iterations = integerError(
    draft.iterations,
    1,
    1_000,
    "Iterations must be a whole number from 1 to 1,000.",
  );
  errors.seed = integerError(
    draft.seed,
    0,
    Number.MAX_SAFE_INTEGER,
    "Seed must be a non-negative safe integer.",
  );

  return Object.fromEntries(
    Object.entries(errors).filter(([, value]) => value !== undefined),
  ) as ScenarioDraftErrors;
}

export function changedDriverCount(draft: ScenarioDraft) {
  return leverFields.reduce(
    (count, field) => count + (numericValue(draft[field]) === 0 ? 0 : 1),
    0,
  );
}

function percentToRate(value: string) {
  return String(numericValue(value) / 100);
}

function slugify(value: string) {
  const slug = value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 66);
  return slug || "policy-experiment";
}

function shortHash(value: string) {
  let hash = 14_695_981_039_346_656_037n;
  for (let index = 0; index < value.length; index += 1) {
    hash = BigInt.asUintN(
      64,
      (hash ^ BigInt(value.charCodeAt(index))) * 1_099_511_628_211n,
    );
  }
  return hash.toString(36).padStart(13, "0");
}

export function scenarioPayload(resource: ScenarioResource): ScenarioPayload {
  return {
    scenario_id: resource.scenario_id,
    name: resource.name,
    company_model_version: resource.company_model_version,
    schema_version: resource.schema_version,
    horizon_days: resource.horizon_days,
    warmup_days: resource.warmup_days,
    evaluation_days: resource.evaluation_days,
    runoff_days: resource.runoff_days,
    baseline_scenario_id: resource.baseline_scenario_id,
    policy_levers: resource.policy_levers,
  };
}

export function buildCandidateScenario(
  draft: ScenarioDraft,
  baseline: ScenarioResource,
): ScenarioPayload {
  const priceChange = numericValue(draft.price_change_percent);
  const capacityChange = numericValue(draft.capacity_change_percent);
  const overtime = numericValue(draft.overtime_minutes);
  const safetyStock = numericValue(draft.safety_stock_days);
  const leadTime = numericValue(
    draft.supplier_lead_time_improvement_percent,
  );
  const supplierCost = numericValue(draft.supplier_cost_change_percent);
  const paymentTerms = numericValue(draft.payment_terms_change_days);
  const capitalInvestment = numericValue(draft.capital_investment_eur);
  const policyLevers = {
    price_changes:
      priceChange === 0
        ? []
        : [
            {
              segment_id: "spot",
              product_id: "intelligent-valve",
              price_change: percentToRate(draft.price_change_percent),
            },
          ],
    commercial_investment_change: percentToRate(
      draft.commercial_investment_percent,
    ),
    resource_changes:
      capacityChange === 0 && overtime === 0
        ? []
        : [
            {
              resource_id: "test",
              regular_capacity_change: percentToRate(
                draft.capacity_change_percent,
              ),
              overtime_capacity_minutes: overtime,
            },
          ],
    material_changes:
      safetyStock === 0 && leadTime === 0 && supplierCost === 0
        ? []
        : [
            {
              material_id: "electronics",
              safety_stock_coverage_days: String(safetyStock),
              supplier_lead_time_improvement: percentToRate(
                draft.supplier_lead_time_improvement_percent,
              ),
              supplier_unit_cost_change: percentToRate(
                draft.supplier_cost_change_percent,
              ),
            },
          ],
    payment_term_changes:
      paymentTerms === 0
        ? []
        : [{ segment_id: "contracted", change_days: paymentTerms }],
    one_off_capital_investment_cents: Math.round(capitalInvestment * 100),
  };
  const identity = JSON.stringify({
    name: draft.name.trim(),
    baseline: {
      scenario_id: baseline.scenario_id,
      company_model_version: baseline.company_model_version,
      schema_version: baseline.schema_version,
      horizon_days: baseline.horizon_days,
      warmup_days: baseline.warmup_days,
      evaluation_days: baseline.evaluation_days,
      runoff_days: baseline.runoff_days,
    },
    policy_levers: policyLevers,
  });

  return {
    scenario_id: `${slugify(draft.name)}-${shortHash(identity)}`,
    name: draft.name.trim(),
    company_model_version: baseline.company_model_version,
    schema_version: baseline.schema_version,
    horizon_days: baseline.horizon_days,
    warmup_days: baseline.warmup_days,
    evaluation_days: baseline.evaluation_days,
    runoff_days: baseline.runoff_days,
    baseline_scenario_id: baseline.scenario_id,
    policy_levers: policyLevers,
  };
}
