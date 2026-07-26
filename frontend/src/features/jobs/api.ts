import { apiRequest } from "../../lib/api";
import type { Job, JobKind, JobStatus } from "./types";

interface JobFilters {
  statuses?: JobStatus[];
  kinds?: JobKind[];
  limit?: number;
}

export function listJobs({
  statuses = [],
  kinds = [],
  limit = 50,
}: JobFilters = {}): Promise<Job[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  for (const status of statuses) {
    query.append("status", status);
  }
  for (const kind of kinds) {
    query.append("kind", kind);
  }
  return apiRequest<Job[]>(`/api/v1/jobs?${query.toString()}`);
}

export function getJob(jobId: string): Promise<Job> {
  return apiRequest<Job>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
}

export function cancelJob(jobId: string): Promise<Job> {
  return apiRequest<Job>(
    `/api/v1/jobs/${encodeURIComponent(jobId)}/cancellation`,
    { method: "POST" },
  );
}

export function getJobResult<T>(job: Job): Promise<T> {
  if (!job.result_location) {
    return Promise.reject(new Error("Job result is not available."));
  }
  return apiRequest<T>(job.result_location);
}
