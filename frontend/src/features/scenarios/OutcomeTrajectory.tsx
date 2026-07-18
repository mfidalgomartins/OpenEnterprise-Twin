import { useId } from "react";

import { formatMetricValue, metricLabels } from "./formatScenario";
import type {
  MetricEntry,
  MetricName,
  ScenarioComparison,
} from "./types";

const trajectoryMetricNames = [
  "ebitda",
  "free_cash_flow",
  "closing_cash",
] as const satisfies readonly MetricName[];

interface Distribution {
  mean: number;
  p10: number;
  p90: number;
}

interface TrajectoryPoint {
  baseline: Distribution;
  candidate: Distribution;
  label: string;
  metricName: (typeof trajectoryMetricNames)[number];
}

interface OutcomeTrajectoryProps {
  comparison: ScenarioComparison;
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
  if (values.length === 0) {
    return { mean, p10: mean, p90: mean };
  }

  return {
    mean,
    p10: quantile(values, 0.1),
    p90: quantile(values, 0.9),
  };
}

function trajectoryPoints(comparison: ScenarioComparison): TrajectoryPoint[] {
  return trajectoryMetricNames.map((metricName) => {
    const metric = comparison.metric_results.find(
      (item) => item.metric_name === metricName,
    );
    const baselineValues = comparison.paired_differences.flatMap(
      (replication) => {
        const value = entryValue(
          replication.baseline_metric_entries,
          metricName,
        );
        return value === undefined ? [] : [value];
      },
    );
    const candidateValues = comparison.paired_differences.flatMap(
      (replication) => {
        const value = entryValue(
          replication.candidate_metric_entries,
          metricName,
        );
        return value === undefined ? [] : [value];
      },
    );

    return {
      baseline: distribution(baselineValues, metric?.baseline_mean ?? 0),
      candidate: distribution(candidateValues, metric?.candidate_mean ?? 0),
      label: metricLabels[metricName],
      metricName,
    };
  });
}

function polyline(
  points: TrajectoryPoint[],
  scenario: "baseline" | "candidate",
  xFor: (index: number) => number,
  yFor: (value: number) => number,
) {
  return points
    .map((point, index) => xFor(index) + "," + yFor(point[scenario].mean))
    .join(" ");
}

function bandPath(
  points: TrajectoryPoint[],
  scenario: "baseline" | "candidate",
  xFor: (index: number) => number,
  yFor: (value: number) => number,
) {
  const upper = points.map(
    (point, index) =>
      (index === 0 ? "M " : "L ") +
      xFor(index) +
      " " +
      yFor(point[scenario].p90),
  );
  const lower = [...points].reverse().map((point, reverseIndex) => {
    const index = points.length - reverseIndex - 1;
    return "L " + xFor(index) + " " + yFor(point[scenario].p10);
  });

  return [...upper, ...lower, "Z"].join(" ");
}

export function OutcomeTrajectory({ comparison }: OutcomeTrajectoryProps) {
  const titleId = useId();
  const descriptionId = useId();
  const points = trajectoryPoints(comparison);
  const improvedCount = points.filter(
    (point) => point.candidate.mean > point.baseline.mean,
  ).length;
  const weakenedCount = points.filter(
    (point) => point.candidate.mean < point.baseline.mean,
  ).length;
  const movement =
    improvedCount === points.length
      ? "improves"
      : weakenedCount === points.length
        ? "weakens"
        : "changes";
  const chartTitle =
    comparison.candidate_scenario_name +
    " " +
    movement +
    " the earnings-to-cash path";
  const summaryMovement =
    improvedCount === points.length
      ? "improves all " + points.length
      : weakenedCount === points.length
        ? "weakens all " + points.length
        : "improves " + improvedCount + " of " + points.length;
  const summary =
    comparison.candidate_scenario_name +
    " " +
    summaryMovement +
    " earnings-to-cash outcomes on average; shaded bands show the P10–P90 range across paired replications.";

  const width = 820;
  const height = 350;
  const margin = { top: 26, right: 132, bottom: 58, left: 66 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const allValues = points.flatMap((point) => [
    point.baseline.p10,
    point.baseline.p90,
    point.candidate.p10,
    point.candidate.p90,
  ]);
  const minimum = Math.min(0, ...allValues);
  const maximum = Math.max(1, ...allValues);
  const range = maximum - minimum || 1;
  const xFor = (index: number) =>
    margin.left + (index * plotWidth) / (points.length - 1);
  const yFor = (value: number) =>
    margin.top + plotHeight - ((value - minimum) / range) * plotHeight;
  const ticks = Array.from({ length: 5 }, (_, index) => {
    const value = minimum + (range * index) / 4;
    return { value, y: yFor(value) };
  }).reverse();
  const lastPoint = points.at(-1);

  return (
    <figure className="outcome-trajectory">
      <figcaption>
        <h3>{chartTitle}</h3>
        <p>{summary}</p>
      </figcaption>
      <svg
        aria-labelledby={titleId + " " + descriptionId}
        className="outcome-trajectory__chart"
        role="img"
        viewBox={"0 0 " + width + " " + height}
      >
        <title id={titleId}>{chartTitle}</title>
        <desc id={descriptionId}>
          {summary} Exact P10, mean and P90 values follow in the table.
        </desc>

        {ticks.map((tick) => (
          <g className="outcome-trajectory__grid" key={tick.value}>
            <line
              x1={margin.left}
              x2={margin.left + plotWidth}
              y1={tick.y}
              y2={tick.y}
            />
            <text x={margin.left - 10} y={tick.y + 4}>
              {formatMetricValue("ebitda", tick.value, { compact: true })}
            </text>
          </g>
        ))}

        <path
          className="outcome-trajectory__band outcome-trajectory__band--baseline"
          d={bandPath(points, "baseline", xFor, yFor)}
        />
        <path
          className="outcome-trajectory__band outcome-trajectory__band--candidate"
          d={bandPath(points, "candidate", xFor, yFor)}
        />
        <polyline
          className="outcome-trajectory__line outcome-trajectory__line--baseline"
          points={polyline(points, "baseline", xFor, yFor)}
        />
        <polyline
          className="outcome-trajectory__line outcome-trajectory__line--candidate"
          points={polyline(points, "candidate", xFor, yFor)}
        />

        {points.map((point, index) => (
          <g className="outcome-trajectory__point" key={point.metricName}>
            <circle
              className="outcome-trajectory__dot outcome-trajectory__dot--baseline"
              cx={xFor(index)}
              cy={yFor(point.baseline.mean)}
              r="3.5"
            />
            <circle
              className="outcome-trajectory__dot outcome-trajectory__dot--candidate"
              cx={xFor(index)}
              cy={yFor(point.candidate.mean)}
              r="3.5"
            />
            <text
              className="outcome-trajectory__axis-label"
              x={xFor(index)}
              y={height - 22}
            >
              {point.label}
            </text>
          </g>
        ))}

        {lastPoint ? (
          <>
            <text
              className="outcome-trajectory__direct-label outcome-trajectory__direct-label--candidate"
              x={margin.left + plotWidth + 12}
              y={yFor(lastPoint.candidate.mean) - 5}
            >
              {comparison.candidate_scenario_name}
            </text>
            <text
              className="outcome-trajectory__direct-label outcome-trajectory__direct-label--baseline"
              x={margin.left + plotWidth + 12}
              y={yFor(lastPoint.baseline.mean) + 14}
            >
              {comparison.baseline_scenario_name}
            </text>
          </>
        ) : null}
      </svg>

      <div aria-hidden="true" className="outcome-trajectory__small-multiples">
        {points.map((point) => {
          const localMinimum = Math.min(
            point.baseline.p10,
            point.candidate.p10,
          );
          const localMaximum = Math.max(
            point.baseline.p90,
            point.candidate.p90,
          );
          const localRange = localMaximum - localMinimum || 1;
          const position = (value: number) =>
            ((value - localMinimum) / localRange) * 100;

          return (
            <section key={point.metricName}>
              <h4>{point.label}</h4>
              {(["candidate", "baseline"] as const).map((scenario) => {
                const values = point[scenario];
                return (
                  <div
                    className={
                      "outcome-trajectory__small-row outcome-trajectory__small-row--" +
                      scenario
                    }
                    key={scenario}
                  >
                    <span>
                      {scenario === "candidate"
                        ? comparison.candidate_scenario_name
                        : comparison.baseline_scenario_name}
                    </span>
                    <span className="outcome-trajectory__small-track">
                      <span
                        className="outcome-trajectory__small-range"
                        style={{
                          insetInlineStart: position(values.p10) + "%",
                          width:
                            Math.max(
                              2,
                              position(values.p90) - position(values.p10),
                            ) + "%",
                        }}
                      />
                      <span
                        className="outcome-trajectory__small-mean"
                        style={{ insetInlineStart: position(values.mean) + "%" }}
                      />
                    </span>
                    <strong>
                      {formatMetricValue(point.metricName, values.mean, {
                        compact: true,
                      })}
                    </strong>
                  </div>
                );
              })}
            </section>
          );
        })}
      </div>

      <table aria-label="Exact earnings-to-cash uncertainty values">
        <caption>Exact P10–P90 replication values</caption>
        <thead>
          <tr>
            <th scope="col">Outcome</th>
            <th scope="col">Scenario</th>
            <th scope="col">P10</th>
            <th scope="col">Mean</th>
            <th scope="col">P90</th>
          </tr>
        </thead>
        <tbody>
          {points.flatMap((point) => [
            <tr key={point.metricName + "-baseline"}>
              <th scope="row">{point.label}</th>
              <td>{comparison.baseline_scenario_name}</td>
              <td>
                {formatMetricValue(point.metricName, point.baseline.p10, {
                  compact: true,
                })}
              </td>
              <td>
                {formatMetricValue(point.metricName, point.baseline.mean, {
                  compact: true,
                })}
              </td>
              <td>
                {formatMetricValue(point.metricName, point.baseline.p90, {
                  compact: true,
                })}
              </td>
            </tr>,
            <tr key={point.metricName + "-candidate"}>
              <th scope="row">{point.label}</th>
              <td>{comparison.candidate_scenario_name}</td>
              <td>
                {formatMetricValue(point.metricName, point.candidate.p10, {
                  compact: true,
                })}
              </td>
              <td>
                {formatMetricValue(point.metricName, point.candidate.mean, {
                  compact: true,
                })}
              </td>
              <td>
                {formatMetricValue(point.metricName, point.candidate.p90, {
                  compact: true,
                })}
              </td>
            </tr>,
          ])}
        </tbody>
      </table>
    </figure>
  );
}
