import { apiRequest } from "../../lib/api";
import type {
  CompanyReference,
  ExecutiveBrief,
  ExperimentResource,
  ScenarioComparison,
  ScenarioPayload,
  ScenarioResource,
} from "./types";

function experimentPath(experimentId: string, resource: "comparison" | "report") {
  return `/api/v1/experiments/${encodeURIComponent(experimentId)}/${resource}`;
}

export function getScenarioComparison(experimentId: string) {
  return apiRequest<ScenarioComparison>(
    experimentPath(experimentId, "comparison"),
  );
}

export function getScenarioReport(experimentId: string) {
  return apiRequest<ExecutiveBrief>(experimentPath(experimentId, "report"));
}

export function getBaselineScenario() {
  return apiRequest<ScenarioResource>("/api/v1/baseline");
}

export function getCompanyReference() {
  return apiRequest<CompanyReference>("/api/v1/company");
}

export function createScenario(scenario: ScenarioPayload) {
  return apiRequest<ScenarioResource>("/api/v1/scenarios", {
    body: scenario,
    method: "POST",
  });
}

export function createExperiment(
  scenarioId: string,
  request: { iterations: number; max_workers: number; seed: number },
  idempotencyKey: string,
) {
  return apiRequest<ExperimentResource>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/experiments`,
    {
      body: request,
      headers: { "Idempotency-Key": idempotencyKey },
      method: "POST",
    },
  );
}

export function getExperiment(experimentId: number) {
  return apiRequest<ExperimentResource>(`/api/v1/experiments/${experimentId}`);
}
