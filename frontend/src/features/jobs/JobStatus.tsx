import type { Job } from "./types";

const statusLabels: Record<Job["status"], string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

function titleCaseStage(stage: string): string {
  const label = stage.replaceAll("_", " ");
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}`;
}

interface JobStatusProps {
  job: Job;
  canCancel?: boolean;
  isCancelling?: boolean;
  onCancel?: () => Promise<unknown> | void;
  compact?: boolean;
}

export function JobStatus({
  job,
  canCancel = false,
  isCancelling = false,
  onCancel,
  compact = false,
}: JobStatusProps) {
  const active = job.status === "queued" || job.status === "running";
  const cancellationAvailable =
    active &&
    canCancel &&
    !job.cancellation_requested_at &&
    onCancel !== undefined;
  const stageLabel = titleCaseStage(job.stage);
  const stateLabel = statusLabels[job.status];

  return (
    <section
      aria-label={`Job ${job.job_id}`}
      className={`job-status job-status--${job.status}${
        compact ? " job-status--compact" : ""
      }`}
    >
      <div className="job-status__headline">
        <span className="job-status__state">{stateLabel}</span>
        {stageLabel !== stateLabel ? (
          <span className="job-status__stage">{stageLabel}</span>
        ) : null}
      </div>

      <div className="job-status__progress">
        <progress
          aria-label="Job progress"
          aria-valuenow={job.progress}
          max={100}
          value={job.progress}
        />
        <span>{job.progress}%</span>
      </div>

      <p className="job-status__attempt">
        Attempt {job.attempt_count} of {job.max_attempts}
      </p>

      {job.cancellation_requested_at ? (
        <p className="job-status__notice">Cancellation requested</p>
      ) : null}

      {job.problem ? (
        <div className="job-status__problem" role="alert">
          <p>{job.problem.detail}</p>
          <code>{job.problem.code}</code>
        </div>
      ) : null}

      <div className="job-status__actions">
        {job.status === "succeeded" && job.result_location ? (
          <a className="job-action" href={job.result_location}>
            Open result
          </a>
        ) : null}
        {cancellationAvailable ? (
          <button
            className="job-action job-action--danger"
            disabled={isCancelling}
            onClick={() => void onCancel()}
            type="button"
          >
            {isCancelling ? "Cancelling…" : "Cancel job"}
          </button>
        ) : null}
      </div>
    </section>
  );
}
