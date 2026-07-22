import { useQuery } from "@tanstack/react-query";

import {
  getBaselineScenario,
  getCompanyReference,
  getDecisionPortfolio,
  getPolicyFrontier,
} from "../scenarios/api";

export function useControlTower() {
  const company = useQuery({
    queryKey: ["company-reference"],
    queryFn: getCompanyReference,
  });
  const baseline = useQuery({
    queryKey: ["baseline-scenario"],
    queryFn: getBaselineScenario,
  });
  const decisions = useQuery({
    queryKey: ["decision-portfolio"],
    queryFn: getDecisionPortfolio,
  });
  const frontier = useQuery({
    queryKey: ["policy-frontier"],
    queryFn: getPolicyFrontier,
  });

  const retry = () => {
    void Promise.all([
      company.refetch(),
      baseline.refetch(),
      decisions.refetch(),
      frontier.refetch(),
    ]);
  };

  return {
    baseline: baseline.data,
    company: company.data,
    decisions: decisions.data,
    error: company.error ?? baseline.error ?? decisions.error ?? frontier.error,
    frontier: frontier.data,
    isPending:
      company.isPending ||
      baseline.isPending ||
      decisions.isPending ||
      frontier.isPending,
    retry,
  };
}
