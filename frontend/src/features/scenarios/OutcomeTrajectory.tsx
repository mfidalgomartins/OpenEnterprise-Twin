import { formatMetricValue, metricLabels } from "./formatScenario";
import type {
  MetricEntry,
  MetricName,
  ScenarioComparison,
} from "./types";

const outcomeMetrics = [
  "ebitda",
  "free_cash_flow",
  "closing_cash",
] as const satisfies readonly MetricName[];

interface Distribution {
  mean: number;
  p10: number;
  p90: number;
}

function entryValue(entries: MetricEntry[], metricName: MetricName) {
  return entries.find(([name]) => name === metricName)?.[1];
}

function quantile(values: number[], probability: number) {
  if (values.length === 0) {
    return 0;
  }
  const sorted = [...values].sort((left, right) => left - right);
  const position = (sorted.length - 1) * probability;
  const lowerIndex = Math.floor(position);
  const upperIndex = Math.ceil(position);
  const lower = sorted[lowerIndex];
  const upper = sorted[upperIndex];
  return lower + (upper - lower) * (position - lowerIndex);
}

function distribution(values: number[], mean: number): Distribution {
  return {
    mean,
    p10: values.length ? quantile(values, 0.1) : mean,
    p90: values.length ? quantile(values, 0.9) : mean,
  };
}

export function OutcomeTrajectory({
  comparison,
}: {
  comparison: ScenarioComparison;
}) {
  const rows = outcomeMetrics.flatMap((metricName) => {
    const metric = comparison.metric_results.find(
      (item) => item.metric_name === metricName,
    );
    const baselineValues = comparison.paired_differences.flatMap((paired) => {
      const value = entryValue(paired.baseline_metric_entries, metricName);
      return value === undefined ? [] : [value];
    });
    const candidateValues = comparison.paired_differences.flatMap((paired) => {
      const value = entryValue(paired.candidate_metric_entries, metricName);
      return value === undefined ? [] : [value];
    });
    return [
      {
        distribution: distribution(baselineValues, metric?.baseline_mean ?? 0),
        metricName,
        scenario: comparison.baseline_scenario_name,
      },
      {
        distribution: distribution(candidateValues, metric?.candidate_mean ?? 0),
        metricName,
        scenario: comparison.candidate_scenario_name,
      },
    ];
  });

  return (
    <figure className="outcome-trajectory">
      <figcaption>
        <h3>Independent outcome intervals</h3>
        <p>
          Each metric has its own scale. P10–P90 intervals preserve uncertainty
          without implying a continuous path between EBITDA, cash flow, and cash.
        </p>
      </figcaption>
      <div className="outcome-trajectory__table-wrap">
        <table>
          <caption>Baseline and candidate distributions by outcome</caption>
          <thead>
            <tr>
              <th scope="col">Outcome</th>
              <th scope="col">Plan</th>
              <th scope="col">P10</th>
              <th scope="col">Mean</th>
              <th scope="col">P90</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.metricName}-${row.scenario}`}>
                <th scope="row">{metricLabels[row.metricName]}</th>
                <td>{row.scenario}</td>
                <td>
                  {formatMetricValue(row.metricName, row.distribution.p10, {
                    compact: true,
                  })}
                </td>
                <td>
                  {formatMetricValue(row.metricName, row.distribution.mean, {
                    compact: true,
                  })}
                </td>
                <td>
                  {formatMetricValue(row.metricName, row.distribution.p90, {
                    compact: true,
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
