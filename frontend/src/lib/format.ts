export interface CurrencyFormatOptions {
  compact?: boolean;
  currency?: string;
  locale?: string;
  maximumFractionDigits?: number;
  signDisplay?: Intl.NumberFormatOptions["signDisplay"];
}

export interface PercentFormatOptions {
  locale?: string;
  maximumFractionDigits?: number;
}

export interface DateFormatOptions {
  locale?: string;
}

export function formatCurrency(
  value: number,
  {
    compact = false,
    currency = "EUR",
    locale = "en-IE",
    maximumFractionDigits = compact ? 0 : 2,
    signDisplay = "auto",
  }: CurrencyFormatOptions = {},
) {
  const formatted = new Intl.NumberFormat(locale, {
    compactDisplay: compact ? "short" : undefined,
    currency,
    maximumFractionDigits,
    notation: compact ? "compact" : "standard",
    signDisplay,
    style: "currency",
  }).format(value);

  return compact ? formatted.replace(/K\b/g, "k") : formatted;
}

export function formatPercent(
  value: number,
  { locale = "en-IE", maximumFractionDigits = 1 }: PercentFormatOptions = {},
) {
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits,
    style: "percent",
  }).format(value);
}

export function formatDate(
  value: string | Date,
  { locale = "en-GB" }: DateFormatOptions = {},
) {
  const date =
    typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? new Date(`${value}T00:00:00.000Z`)
      : new Date(value);

  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(date);
}
