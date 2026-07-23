const EUR = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});

export function formatCents(value: number): string {
  return EUR.format(value / 100);
}

export function formatMoney(value: number): string {
  return EUR.format(value);
}

export function formatPercent(value: number, fractionDigits = 1): string {
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

export function formatNumber(value: number, fractionDigits = 2): string {
  return value.toLocaleString("en-IE", {
    maximumFractionDigits: fractionDigits,
  });
}

export function formatSignedMoney(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${EUR.format(value)}`;
}

export function titleCase(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

const METRIC_LABELS: Record<string, string> = {
  ebitda: "EBITDA",
  otif: "OTIF",
  revenue: "Revenue",
  free_cash_flow: "Free cash flow",
  closing_cash: "Closing cash",
  backlog_units: "Backlog",
  capacity_utilization: "Capacity utilisation",
  peak_revolver: "Peak revolver",
  rescue_funding: "Rescue funding",
  cancellation_rate: "Cancellation rate",
};

export function metricLabel(name: string): string {
  return METRIC_LABELS[name] ?? titleCase(name);
}

export function metricIsMoney(name: string): boolean {
  return [
    "ebitda",
    "revenue",
    "free_cash_flow",
    "closing_cash",
    "peak_revolver",
    "rescue_funding",
  ].includes(name);
}
