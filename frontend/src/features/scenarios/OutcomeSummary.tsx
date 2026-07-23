import { formatPercent } from "../../lib/format";
import { formatMetricValue, metricLabels } from "./formatScenario";
import type { MetricComparison, MetricName, OutcomeDelta } from "./types";

const outcomePriority: readonly MetricName[] = [
  "ebitda",
  "closing_cash",
  "otif",
];

interface OutcomeSummaryProps {
  metrics: MetricComparison[];
  outcomes: OutcomeDelta[];
}

export function OutcomeSummary({ metrics, outcomes }: OutcomeSummaryProps) {
  const selectedOutcomes = outcomePriority.flatMap((metricName) => {
    const outcome = outcomes.find((item) => item.metric_name === metricName);
    return outcome ? [outcome] : [];
  });

  return (
    <dl aria-label="Key outcomes" className="outcome-summary" role="list">
      {selectedOutcomes.map((outcome) => {
        const metric = metrics.find(
          (item) => item.metric_name === outcome.metric_name,
        );
        const isRisk = (metric?.candidate_breach_probability ?? 0) >= 0.5;
        return (
        <div
          className={
            "outcome-summary__item outcome-summary__item--" +
            (isRisk
              ? "negative"
              : outcome.mean_difference >= 0
                ? "positive"
                : "negative")
          }
          key={outcome.metric_name}
          role="listitem"
        >
          <dt>{metricLabels[outcome.metric_name]}</dt>
          <dd>
            {formatMetricValue(outcome.metric_name, outcome.candidate_mean, {
              compact: true,
            })}
          </dd>
          <small>
            Baseline {formatMetricValue(outcome.metric_name, outcome.baseline_mean, {
              compact: true,
            })} · delta {formatMetricValue(outcome.metric_name, outcome.mean_difference, {
              compact: true,
              difference: true,
            })}
          </small>
          <small>
            {formatPercent(metric?.candidate_breach_probability ?? 0, {
              maximumFractionDigits: 0,
            })} guardrail breach risk · {formatPercent(
              outcome.probability_of_improvement,
              { maximumFractionDigits: 0 },
            )} chance of improvement
          </small>
        </div>
        );
      })}
    </dl>
  );
}
