import { formatPercent } from "../../lib/format";
import { formatMetricValue, metricLabels } from "./formatScenario";
import type { MetricName, OutcomeDelta } from "./types";

const outcomePriority: readonly MetricName[] = [
  "ebitda",
  "closing_cash",
  "otif",
];

interface OutcomeSummaryProps {
  outcomes: OutcomeDelta[];
}

export function OutcomeSummary({ outcomes }: OutcomeSummaryProps) {
  const selectedOutcomes = outcomePriority.flatMap((metricName) => {
    const outcome = outcomes.find((item) => item.metric_name === metricName);
    return outcome ? [outcome] : [];
  });

  return (
    <dl aria-label="Key outcomes" className="outcome-summary" role="list">
      {selectedOutcomes.map((outcome) => (
        <div
          className={
            "outcome-summary__item outcome-summary__item--" +
            (outcome.mean_difference >= 0 ? "positive" : "negative")
          }
          key={outcome.metric_name}
          role="listitem"
        >
          <dt>{metricLabels[outcome.metric_name]}</dt>
          <dd>
            {formatMetricValue(outcome.metric_name, outcome.mean_difference, {
              compact: true,
              difference: true,
            })}
          </dd>
          <small>
            {formatPercent(outcome.probability_of_improvement, {
              maximumFractionDigits: 0,
            })}{" "}
            probability of improvement
          </small>
        </div>
      ))}
    </dl>
  );
}
