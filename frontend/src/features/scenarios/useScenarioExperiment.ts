import { useCallback, useState } from "react";

import { ApiError } from "../../lib/api";
import {
  createExperiment,
  createScenario,
  getExperiment,
} from "./api";
import { scenarioPayload } from "./scenarioDraft";
import type {
  ExperimentResource,
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

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

async function ensureScenario(scenario: ScenarioPayload) {
  try {
    await createScenario(scenario);
  } catch (error) {
    if (error instanceof ApiError && error.code === "scenario_conflict") {
      return;
    }
    throw error;
  }
}

function experimentFailure(experiment: ExperimentResource): RunIssue {
  return {
    code: experiment.error_code ?? "experiment_execution",
    detail: experiment.error_detail ?? "Experiment execution failed.",
    correctiveAction:
      "Inputs remain saved. Review the model limits and retry the experiment.",
  };
}

async function waitForCompletion(
  initial: ExperimentResource,
  pollIntervalMs: number,
) {
  let experiment = initial;
  const deadline = Date.now() + 15 * 60 * 1_000;
  while (experiment.status === "queued" || experiment.status === "running") {
    if (Date.now() >= deadline) {
      throw {
        code: "experiment_timeout",
        detail: "Experiment did not complete within 15 minutes.",
        correctiveAction: correctiveActions.experiment_timeout,
      } satisfies RunIssue;
    }
    await delay(pollIntervalMs);
    experiment = await getExperiment(experiment.id);
  }
  if (experiment.status === "failed") {
    throw experimentFailure(experiment);
  }
  return experiment;
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

export function useScenarioExperiment(pollIntervalMs = 500) {
  const [phase, setPhase] = useState<ExperimentPhase>("idle");
  const [issue, setIssue] = useState<RunIssue | null>(null);
  const [lastCompleted, setLastCompleted] =
    useState<LastCompletedExperiment | null>(null);

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
        await waitForCompletion(baselineExperiment, pollIntervalMs);

        setPhase("saving_candidate");
        await ensureScenario(candidate);
        setPhase("running_candidate");
        const candidateExperiment = await createExperiment(
          candidate.scenario_id,
          request,
          `candidate-${candidate.scenario_id}-${seed}-${iterations}`,
        );
        const completed = await waitForCompletion(
          candidateExperiment,
          pollIntervalMs,
        );
        setLastCompleted({
          experimentId: completed.id,
          scenarioId: candidate.scenario_id,
        });
        setPhase("completed");
      } catch (error) {
        setIssue(toRunIssue(error));
        setPhase("failed");
      }
    },
    [pollIntervalMs],
  );

  return {
    isRunning: !["idle", "completed", "failed"].includes(phase),
    issue,
    lastCompleted,
    phase,
    runScenario,
  };
}
