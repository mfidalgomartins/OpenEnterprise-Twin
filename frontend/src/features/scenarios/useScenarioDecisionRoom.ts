import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getScenarioComparison, getScenarioReport } from "./api";

export function useScenarioDecisionRoom(experimentId: string) {
  const enabled = experimentId.length > 0;
  const comparisonQuery = useQuery({
    enabled,
    placeholderData: keepPreviousData,
    queryFn: () => getScenarioComparison(experimentId),
    queryKey: ["scenario-comparison", experimentId],
  });
  const reportQuery = useQuery({
    enabled,
    placeholderData: keepPreviousData,
    queryFn: () => getScenarioReport(experimentId),
    queryKey: ["scenario-report", experimentId],
  });

  return {
    comparison: comparisonQuery.data,
    error: comparisonQuery.error ?? reportQuery.error,
    isFetching: comparisonQuery.isFetching || reportQuery.isFetching,
    isPending: comparisonQuery.isPending || reportQuery.isPending,
    report: reportQuery.data,
  };
}
