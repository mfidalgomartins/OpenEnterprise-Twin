import { formatCurrency, formatPercent } from "../../lib/format";
import type { MetricName } from "./types";

export const metricLabels: Record<MetricName, string> = {
  revenue: "Revenue",
  ebitda: "EBITDA",
  free_cash_flow: "Free cash flow",
  closing_cash: "Closing cash",
  otif: "OTIF",
  cancellation_rate: "Cancellation rate",
  backlog_units: "Backlog units",
  capacity_utilization: "Capacity utilization",
  peak_revolver: "Peak revolver",
  rescue_funding: "Rescue funding",
};

const monetaryMetrics = new Set<MetricName>([
  "revenue",
  "ebitda",
  "free_cash_flow",
  "closing_cash",
  "peak_revolver",
  "rescue_funding",
]);

const percentageMetrics = new Set<MetricName>([
  "otif",
  "cancellation_rate",
  "capacity_utilization",
]);

interface FormatMetricOptions {
  compact?: boolean;
  difference?: boolean;
}

export function formatMetricValue(
  metricName: MetricName,
  value: number,
  { compact = false, difference = false }: FormatMetricOptions = {},
) {
  if (monetaryMetrics.has(metricName)) {
    return formatCurrency(value / 100, {
      compact,
      maximumFractionDigits: compact ? 0 : 0,
      signDisplay: difference ? "always" : "auto",
    });
  }

  if (percentageMetrics.has(metricName)) {
    if (difference) {
      const points = value * 100;
      const formatted = new Intl.NumberFormat("en-IE", {
        maximumFractionDigits: 1,
        signDisplay: "always",
      }).format(points);
      return `${formatted} pp`;
    }
    return formatPercent(value, { maximumFractionDigits: 1 });
  }

  return new Intl.NumberFormat("en-IE", {
    maximumFractionDigits: 1,
    signDisplay: difference ? "always" : "auto",
  }).format(value);
}

export function formatInteger(value: number) {
  return new Intl.NumberFormat("en-IE", { maximumFractionDigits: 0 }).format(
    value,
  );
}
