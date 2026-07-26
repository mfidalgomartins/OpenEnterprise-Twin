import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { cancelJob, getJob, getJobResult } from "./api";
import { isActiveJob, type Job } from "./types";

export function pollIntervalForJob(job: Job | undefined): number | false {
  if (!job || !isActiveJob(job)) {
    return false;
  }
  return job.status === "queued" ? 1_000 : 750;
}

export function useJob(jobId: string | null, initialJob?: Job | null) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId as string),
    enabled:
      jobId !== null &&
      (initialJob === undefined ||
        initialJob === null ||
        isActiveJob(initialJob)),
    initialData:
      initialJob && initialJob.job_id === jobId ? initialJob : undefined,
    refetchInterval: (query) => pollIntervalForJob(query.state.data),
    refetchIntervalInBackground: false,
  });
}

export function useJobResult<T>(job: Job | undefined) {
  return useQuery({
    queryKey: ["job-result", job?.job_id],
    queryFn: () => getJobResult<T>(job as Job),
    enabled: job?.status === "succeeded" && Boolean(job.result_location),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useTrackedJob<TResult>() {
  const queryClient = useQueryClient();
  const [submittedJob, setSubmittedJob] = useState<Job | null>(null);
  const track = useCallback(
    (job: Job) => {
      setSubmittedJob(job);
      queryClient.setQueryData(["job", job.job_id], job);
    },
    [queryClient],
  );
  const jobQuery = useJob(submittedJob?.job_id ?? null, submittedJob);
  const currentJob = jobQuery.data ?? submittedJob ?? undefined;
  const resultQuery = useJobResult<TResult>(currentJob);
  const cancellation = useMutation({
    mutationFn: () => cancelJob(currentJob?.job_id ?? ""),
    onSuccess: track,
  });

  return {
    cancel: cancellation.mutateAsync,
    cancellation,
    currentJob,
    isActive: currentJob ? isActiveJob(currentJob) : false,
    jobQuery,
    resultQuery,
    track,
  };
}
