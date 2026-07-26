export type JobKind =
  | "experiment"
  | "calibration"
  | "optimization"
  | "adaptive_comparison";

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface JobProblem {
  code: string;
  detail: string;
  occurred_at?: string;
}

export interface Job {
  job_id: string;
  kind: JobKind;
  status: JobStatus;
  created_by: string;
  attempt_count: number;
  max_attempts: number;
  progress: number;
  stage: string;
  cancellation_requested_at: string | null;
  next_attempt_at: string | null;
  result_resource_type: string | null;
  result_resource_id: string | null;
  result_digest: string | null;
  result_location: string | null;
  problem: JobProblem | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export const terminalJobStatuses: ReadonlySet<JobStatus> = new Set([
  "succeeded",
  "failed",
  "cancelled",
]);

export function isActiveJob(job: Job): boolean {
  return !terminalJobStatuses.has(job.status);
}
