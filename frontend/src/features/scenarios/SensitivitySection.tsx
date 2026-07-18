import { formatMetricValue, metricLabels } from "./formatScenario";
import type { MetricComparison, MetricName } from "./types";

const financialMetrics = new Set<MetricName>([
  "revenue",
  "ebitda",
  "free_cash_flow",
  "closing_cash",
  "peak_revolver",
  "rescue_funding",
]);

interface SensitivitySectionProps {
  metrics: MetricComparison[];
}

export function SensitivitySection({ metrics }: SensitivitySectionProps) {
  const rankedMetrics = [...metrics]
    .filter((metric) => financialMetrics.has(metric.metric_name))
    .sort(
      (left, right) =>
        right.p95_difference -
        right.p5_difference -
        (left.p95_difference - left.p5_difference),
    )
    .slice(0, 5);
  const maximumMagnitude = Math.max(
    1,
    ...rankedMetrics.flatMap((metric) => [
      Math.abs(metric.p5_difference),
      Math.abs(metric.p95_difference),
    ]),
  );

  return (
    <section aria-labelledby="sensitivities-title" className="decision-chapter">
      <div className="decision-chapter__heading">
        <h2 id="sensitivities-title">Sensitivities</h2>
        <p>
          Financial outcome ranges are ranked by P5–P95 spread. They describe
          simulation uncertainty, not unmeasured driver elasticity.
        </p>
      </div>
      <ol className="sensitivity-list">
        {rankedMetrics.map((metric) => {
          const left = 50 + (metric.p5_difference / maximumMagnitude) * 50;
          const right = 50 + (metric.p95_difference / maximumMagnitude) * 50;
          const start = Math.min(left, right);
          const width = Math.max(1, Math.abs(right - left));

          return (
            <li key={metric.metric_name}>
              <div className="sensitivity-list__label">
                <strong>{metricLabels[metric.metric_name]}</strong>
                <span>
                  {formatMetricValue(
                    metric.metric_name,
                    metric.p5_difference,
                    { compact: true, difference: true },
                  )}{" "}
                  to{" "}
                  {formatMetricValue(
                    metric.metric_name,
                    metric.p95_difference,
                    { compact: true, difference: true },
                  )}
                </span>
              </div>
              <div
                aria-label={
                  metricLabels[metric.metric_name] +
                  " paired difference range from " +
                  formatMetricValue(
                    metric.metric_name,
                    metric.p5_difference,
                    { compact: true, difference: true },
                  ) +
                  " to " +
                  formatMetricValue(
                    metric.metric_name,
                    metric.p95_difference,
                    { compact: true, difference: true },
                  )
                }
                className="sensitivity-list__track"
              >
                <span className="sensitivity-list__zero" />
                <span
                  className={
                    "sensitivity-list__range" +
                    (metric.p5_difference < 0
                      ? " sensitivity-list__range--risk"
                      : "")
                  }
                  style={{
                    insetInlineStart: start + "%",
                    width: width + "%",
                  }}
                />
              </div>
            </li>
          );
        })}
      </ol>
      <p className="sensitivity-list__axis" aria-hidden="true">
        <span>Downside</span>
        <span>No change</span>
        <span>Upside</span>
      </p>
    </section>
  );
}
