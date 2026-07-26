import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../../lib/api";
import { useJob } from "../jobs/useJob";
import type { Job } from "../jobs/types";
import {
  createExperiment,
  createScenario,
  getScenario,
} from "./api";
import { scenarioPayload } from "./scenarioDraft";
import type {
  ScenarioPayload,
  ScenarioResource,
} from "./types";

export type ExperimentPhase =
  | "idle"
  | "saving_baseline"
  | "running_baseline"
  | "saving_candidate"
  | "running_candidate"
  | "completed"
  | "failed";

export interface RunIssue {
  code: string;
  correctiveAction: string;
  detail: string;
}

export interface LastCompletedExperiment {
  experimentId: number;
  scenarioId: string;
}

interface ScenarioExperimentInput {
  baseline: ScenarioResource;
  candidate: ScenarioPayload;
  iterations: number;
  seed: number;
}

const correctiveActions: Record<string, string> = {
  baseline_experiment_incompatible:
    "Re-run the baseline with the same model calendar, seed, and iterations.",
  baseline_experiment_missing:
    "Run the baseline first with the same seed and iterations.",
  experiment_queue_full:
    "Wait for an active experiment to finish, then retry this saved draft.",
  experiment_timeout:
    "Check the experiment status from the API and retry after capacity is available.",
  scenario_conflict:
    "Change a driver or scenario name to create a distinct immutable revision.",
  scenario_incompatible:
    "Review the highlighted lever limits and try again.",
};

function immutableScenarioConflict(): RunIssue {
  return {
    code: "scenario_conflict",
    detail:
      "The immutable scenario identifier is already bound to different inputs.",
    correctiveAction: correctiveActions.scenario_conflict,
  };
}

function normalizeDecimalString(value: string) {
  const match = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(value);
  if (!match || (!match[2] && !match[3])) {
    return value;
  }
  const integer = (match[2] || "0").replace(/^0+(?=\d)/, "");
  const fraction = (match[3] ?? "").replace(/0+$/, "");
  const sign = match[1] === "-" && (integer !== "0" || fraction) ? "-" : "";
  return `${sign}${integer}${fraction ? `.${fraction}` : ""}`;
}

const decimalPolicyFields = new Set([
  "commercial_investment_change",
  "price_change",
  "regular_capacity_change",
  "safety_stock_coverage_days",
  "supplier_lead_time_improvement",
  "supplier_unit_cost_change",
]);

function canonicalPolicyValue(value: unknown, fieldName?: string): unknown {
  if (typeof value === "string") {
    return decimalPolicyFields.has(fieldName ?? "")
      ? normalizeDecimalString(value)
      : value;
  }
  if (Array.isArray(value)) {
    return value.map((entry) => canonicalPolicyValue(entry));
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, canonicalPolicyValue(entry, key)]),
    );
  }
  return value;
}

function scenarioMatches(
  existing: ScenarioResource,
  expected: ScenarioPayload,
) {
  const current = scenarioPayload(existing);
  return (
    current.scenario_id === expected.scenario_id &&
    current.name === expected.name &&
    current.company_model_version === expected.company_model_version &&
    current.schema_version === expected.schema_version &&
    current.horizon_days === expected.horizon_days &&
    current.warmup_days === expected.warmup_days &&
    current.evaluation_days === expected.evaluation_days &&
    current.runoff_days === expected.runoff_days &&
    current.baseline_scenario_id === expected.baseline_scenario_id &&
    JSON.stringify(canonicalPolicyValue(current.policy_levers)) ===
      JSON.stringify(canonicalPolicyValue(expected.policy_levers))
  );
}

async function ensureScenario(scenario: ScenarioPayload) {
  try {
    const existing = await getScenario(scenario.scenario_id);
    if (!scenarioMatches(existing, scenario)) {
      throw immutableScenarioConflict();
    }
    return;
  } catch (error) {
    if (!(error instanceof ApiError) || error.code !== "scenario_not_found") {
      throw error;
    }
  }
  try {
    await createScenario(scenario);
  } catch (error) {
    if (!(error instanceof ApiError) || error.code !== "scenario_conflict") {
      throw error;
    }
    const existing = await getScenario(scenario.scenario_id);
    if (!scenarioMatches(existing, scenario)) {
      throw immutableScenarioConflict();
    }
  }
}

function jobFailure(job: Job): RunIssue {
  return {
    code: job.problem?.code ?? `experiment_${job.status}`,
    detail:
      job.problem?.detail ??
      (job.status === "cancelled"
        ? "Experiment execution was cancelled."
        : "Experiment execution failed."),
    correctiveAction:
      "Inputs remain saved. Review the model limits and retry the experiment.",
  };
}

function toRunIssue(error: unknown): RunIssue {
  if (error instanceof ApiError) {
    return {
      code: error.code,
      detail: error.message,
      correctiveAction:
        correctiveActions[error.code] ??
        "Inputs remain saved. Check the service status and retry.",
    };
  }
  if (
    error &&
    typeof error === "object" &&
    "code" in error &&
    "detail" in error &&
    "correctiveAction" in error
  ) {
    return error as RunIssue;
  }
  return {
    code: "client_execution",
    detail: "The comparison could not be completed.",
    correctiveAction:
      "Inputs remain saved. Check the service status and retry.",
  };
}

interface PendingCandidate {
  candidate: ScenarioPayload;
  idempotencyKey: string;
  request: { iterations: number; max_workers: number; seed: number };
}

export function useScenarioExperiment() {
  const [phase, setPhase] = useState<ExperimentPhase>("idle");
  const [issue, setIssue] = useState<RunIssue | null>(null);
  const [lastCompleted, setLastCompleted] =
    useState<LastCompletedExperiment | null>(null);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [activeStep, setActiveStep] = useState<"baseline" | "candidate" | null>(
    null,
  );
  const pendingCandidate = useRef<PendingCandidate | null>(null);
  const transitioningJobId = useRef<string | null>(null);
  const jobQuery = useJob(activeJob?.job_id ?? null, activeJob);
  const observedJob = jobQuery.data ?? activeJob;

  const runScenario = useCallback(
    async ({
      baseline,
      candidate,
      iterations,
      seed,
    }: ScenarioExperimentInput) => {
      setIssue(null);
      setPhase("saving_baseline");
      try {
        await ensureScenario(scenarioPayload(baseline));
        setPhase("running_baseline");
        const request = { iterations, seed, max_workers: 1 };
        const baselineExperiment = await createExperiment(
          baseline.scenario_id,
          request,
          `baseline-${baseline.schema_version}-${seed}-${iterations}`,
        );
        pendingCandidate.current = {
          candidate,
          idempotencyKey: `candidate-${candidate.scenario_id}-${seed}-${iterations}`,
          request,
        };
        transitioningJobId.current = null;
        setActiveStep("baseline");
        setActiveJob(baselineExperiment);
      } catch (error) {
        setIssue(toRunIssue(error));
        setPhase("failed");
      }
    },
    [],
  );

  useEffect(() => {
    if (
      !observedJob ||
      !activeStep ||
      transitioningJobId.current === observedJob.job_id
    ) {
      return;
    }
    let active = true;
    const advance = async () => {
      if (
        observedJob.status === "failed" ||
        observedJob.status === "cancelled"
      ) {
        transitioningJobId.current = observedJob.job_id;
        if (active) {
          setIssue(jobFailure(observedJob));
          setPhase("failed");
        }
        return;
      }
      if (observedJob.status !== "succeeded") {
        return;
      }

      transitioningJobId.current = observedJob.job_id;
      if (activeStep === "candidate") {
        const experimentId = Number(observedJob.result_resource_id);
        const candidate = pendingCandidate.current?.candidate;
        if (
          !Number.isInteger(experimentId) ||
          experimentId <= 0 ||
          !candidate
        ) {
          if (active) {
            setIssue({
              code: "experiment_result_invalid",
              detail: "The completed job did not return a valid experiment.",
              correctiveAction:
                "Open the Jobs workspace to inspect the durable result reference.",
            });
            setPhase("failed");
          }
          return;
        }
        pendingCandidate.current = null;
        if (active) {
          setLastCompleted({
            experimentId,
            scenarioId: candidate.scenario_id,
          });
          setActiveJob(null);
          setActiveStep(null);
          setPhase("completed");
        }
        return;
      }

      const pending = pendingCandidate.current;
      if (!pending) {
        throw new Error("candidate submission context is missing");
      }
      if (active) {
        setPhase("saving_candidate");
      }
      await ensureScenario(pending.candidate);
      if (active) {
        setPhase("running_candidate");
      }
      const candidateJob = await createExperiment(
        pending.candidate.scenario_id,
        pending.request,
        pending.idempotencyKey,
      );
      transitioningJobId.current = null;
      if (active) {
        setActiveStep("candidate");
        setActiveJob(candidateJob);
      }
    };
    void advance().catch((error: unknown) => {
      if (active) {
        setIssue(toRunIssue(error));
        setPhase("failed");
      }
    });
    return () => {
      active = false;
    };
  }, [activeStep, observedJob]);

  return {
    activeJob: observedJob,
    isRunning: !["idle", "completed", "failed"].includes(phase),
    issue,
    lastCompleted,
    phase,
    runScenario,
  };
}
