import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { useAuth } from "../auth/authContext";
import { cancelJob, listJobs } from "./api";
import { JobStatus } from "./JobStatus";
import {
  isActiveJob,
  type Job,
  type JobKind,
  type JobStatus as JobState,
} from "./types";

const kindLabels: Record<JobKind, string> = {
  experiment: "Experiment",
  calibration: "Calibration",
  optimization: "Optimization",
  adaptive_comparison: "Adaptive comparison",
};

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function JobsPage() {
  const { can, session } = useAuth();
  const queryClient = useQueryClient();
  const [mineOnly, setMineOnly] = useState(true);
  const [statusFilter, setStatusFilter] = useState<JobState | "">("");
  const [kindFilter, setKindFilter] = useState<JobKind | "">("");
  const jobs = useQuery({
    queryKey: ["jobs", statusFilter, kindFilter],
    queryFn: () =>
      listJobs({
        statuses: statusFilter ? [statusFilter] : [],
        kinds: kindFilter ? [kindFilter] : [],
      }),
    refetchInterval: (query) =>
      query.state.data?.some(isActiveJob) ? 2_000 : false,
  });
  const cancellation = useMutation({
    mutationFn: cancelJob,
    onSuccess: (updated) => {
      queryClient.setQueriesData<Job[]>({ queryKey: ["jobs"] }, (current) =>
        current?.map((item) =>
          item.job_id === updated.job_id ? updated : item,
        ),
      );
      queryClient.setQueryData(["job", updated.job_id], updated);
    },
  });

  const visibleJobs = useMemo(
    () =>
      (jobs.data ?? []).filter(
        (job) => !mineOnly || job.created_by === session?.subject,
      ),
    [jobs.data, mineOnly, session?.subject],
  );

  return (
    <div className="jobs-page">
      <header className="jobs-page__header">
        <div>
          <p className="jobs-page__eyebrow">Durable execution</p>
          <h1>Analytical jobs</h1>
          <p>
            Tenant <strong>{session?.tenant_id ?? "—"}</strong> · persistent,
            recoverable workloads with traceable results.
          </p>
        </div>
        <div className="jobs-page__signal">
          <span>{visibleJobs.filter(isActiveJob).length}</span>
          active
        </div>
      </header>

      <div className="jobs-filters" aria-label="Job filters">
        <label>
          <span>Status</span>
          <select
            value={statusFilter}
            onChange={(event) =>
              setStatusFilter(event.target.value as JobState | "")
            }
          >
            <option value="">All states</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
        <label>
          <span>Workload</span>
          <select
            value={kindFilter}
            onChange={(event) =>
              setKindFilter(event.target.value as JobKind | "")
            }
          >
            <option value="">All workloads</option>
            {Object.entries(kindLabels).map(([kind, label]) => (
              <option key={kind} value={kind}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="jobs-filters__mine">
          <input
            checked={mineOnly}
            onChange={(event) => setMineOnly(event.target.checked)}
            type="checkbox"
          />
          <span>Created by me</span>
        </label>
      </div>

      {jobs.isPending ? (
        <p className="jobs-page__state" role="status">
          Loading analytical jobs…
        </p>
      ) : jobs.isError ? (
        <div className="jobs-page__state jobs-page__state--error" role="alert">
          <p>Jobs could not be loaded.</p>
          <button onClick={() => void jobs.refetch()} type="button">
            Retry
          </button>
        </div>
      ) : visibleJobs.length === 0 ? (
        <p className="jobs-page__state">
          No jobs match the current identity and filters.
        </p>
      ) : (
        <div className="jobs-table-wrap">
          <table className="jobs-table">
            <caption>Tenant-scoped analytical workload history</caption>
            <thead>
              <tr>
                <th scope="col">Workload</th>
                <th scope="col">Owner</th>
                <th scope="col">Submitted</th>
                <th scope="col">Lifecycle</th>
              </tr>
            </thead>
            <tbody>
              {visibleJobs.map((job) => (
                <tr key={job.job_id}>
                  <td data-label="Workload">
                    <strong>{kindLabels[job.kind]}</strong>
                    <code className="jobs-table__id">{job.job_id}</code>
                  </td>
                  <td data-label="Owner">{job.created_by}</td>
                  <td data-label="Submitted">{formatDate(job.created_at)}</td>
                  <td data-label="Lifecycle">
                    <JobStatus
                      canCancel={can("analyst", "admin")}
                      compact
                      isCancelling={
                        cancellation.isPending &&
                        cancellation.variables === job.job_id
                      }
                      job={job}
                      onCancel={() => cancellation.mutateAsync(job.job_id)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
