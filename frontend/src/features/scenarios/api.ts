import { apiRequest } from "../../lib/api";
import type { ExecutiveBrief, ScenarioComparison } from "./types";

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
