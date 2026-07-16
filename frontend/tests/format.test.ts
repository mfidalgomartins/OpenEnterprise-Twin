import { formatCurrency, formatDate, formatPercent } from "../src/lib/format";

describe("format helpers", () => {
  it("formats compact currency deltas with tabular-friendly executive notation", () => {
    expect(
      formatCurrency(184_000, {
        compact: true,
        signDisplay: "always",
      }),
    ).toBe("+€184k");
  });

  it("formats ratios as percentages", () => {
    expect(formatPercent(0.974, { maximumFractionDigits: 1 })).toBe("97.4%");
  });

  it("formats date-only values without a timezone shift", () => {
    expect(formatDate("2025-05-16", { locale: "en-US" })).toBe("May 16, 2025");
  });
});
