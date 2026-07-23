# OpenEnterprise Twin Design System

This document defines the implemented visual and interaction rules for the 0.2 control tower, policy studio, Decision Room and executive brief.

## Design idea

The interface should feel like a bright boardroom during a working session: evidence on a white surface, graphite documentation and restrained green marks where a decision is being made. The product is an interactive decision brief, not a dashboard wall.

## Tokens

```css
:root {
  --color-canvas: oklch(1 0 0);
  --color-surface: oklch(0.965 0.004 140);
  --color-ink: oklch(0.18 0.012 145);
  --color-muted: oklch(0.45 0.012 145);
  --color-line: oklch(0.88 0.006 145);
  --color-decision: oklch(0.48 0.105 140);
  --color-decision-soft: oklch(0.94 0.035 140);
  --color-comparison: oklch(0.52 0.12 250);
  --color-risk: oklch(0.52 0.16 30);
  --color-warning: oklch(0.61 0.14 70);

  --font-sans: "Inter Variable", Inter, ui-sans-serif, system-ui, sans-serif;
  --text-meta: 0.75rem;
  --text-ui: 0.875rem;
  --text-body: 1rem;
  --text-section: 1.25rem;
  --text-decision: clamp(1.5rem, 2.1vw, 1.875rem);
  --text-report: 2.25rem;

  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-12: 3rem;

  --radius-control: 0.5rem;
  --radius-panel: 0.75rem;
  --motion-state: 180ms;
}
```

Text uses weights 400, 500 and 600 only. Numeric content uses tabular figures. Body text targets 7:1 contrast where practical; no essential distinction relies on color alone.

## Layout

- A 64px horizontal product header preserves analytical width. There is no sidebar.
- The desktop decision page uses `minmax(0, 1fr) 320px` up to a 1600px content maximum.
- The decision rail is sticky on large screens and follows the headline on tablet and mobile.
- Narrative prose stays below 75 characters per line; charts may expand into available width.
- Chapters are divided by whitespace and one-pixel rules. A card is permitted only when its boundary communicates a distinct operational state.

## Component families

### Product header

The left side contains the wordmark and five destinations: Briefing, Twin, Scenarios, Decisions and Reports. The right side shows the loaded company, currency, model version and explicit synthetic-reference data mode. Navigation selection is expressed with typography and a thin green rule.

### Decision header

The scenario name and lifecycle state lead. Baseline, horizon and freshness form one metadata line. The decision sentence is the strongest typographic element on the page and must contain an action, quantified outcome and material trade-off.

### Outcome summary

At most three outcomes appear inline below the decision sentence. They are not boxed cards. Each has a quiet label, tabular value and semantic color only when the direction is meaningful.

### Analytical chart

Outcome evidence uses independent per-metric P10, mean and P90 rows so unlike monetary stocks and flows are never presented as one continuous path. Sensitivity ranges are ranked and retain exact values and accessible labels.

### Narrative chapters

The primary chapter order is Impact, Mechanism, Sensitivities, Execution and Evidence. The mechanism uses an ordered causal chain, sensitivities use ranked ranges, and evidence closes with model, seed, iterations and assumption provenance.

### Decision rail

The only exceptional panel contains recommendation, downside trigger, binding constraint, owner, review date and record action. It uses a 12px radius, no decorative shadow and no gradient. The recommendation is `Adopt`, `Pilot only` or `Do not adopt`; exploratory evidence can never authorize adoption.

## Interaction

- Recalculation retains the last valid result and announces status through `aria-live="polite"`.
- Changed assumptions show saved time and changed-driver count.
- Errors preserve entered values and name the failed operation with a corrective action.
- Primary links and actions navigate to real routes or execute the named operation; unavailable capabilities are not presented as controls.
- Published reports are immutable and display the complete reproducibility record.
- State transitions run between 150 and 250ms and are disabled under reduced motion.

## Prohibited patterns

- Sidebar navigation, bento grids and repeated KPI cards.
- Hero eyebrows, decorative badges and icon rows.
- Glass, gradients, ornamental dark mode and navy-and-gold styling.
- Gauges, donuts, radar charts, 3D charts, dual axes and unexplained confidence scores.
- Generic claims such as “unlock value”, “transform operations” or “AI-powered insights”.
- Chat-first interaction or generated prose without linked evidence.

## Responsive behavior

- At 1280px and above, show the complete canvas and sticky rail.
- From 768px to 1279px, place the rail below the decision sentence and keep comparisons non-linear.
- Below 768px, use a reading-first sequence with assumptions in ordered sections and charts converted to ranked or small-multiple forms.
- Evidence, risk and provenance are reordered on smaller screens, never hidden.

## Visual acceptance

The release is accepted only after real application captures are inspected at 1440×1000, 1024×900 and 390×844. Typography, spacing, color, tables, responsive order and interaction states must have no overflow, clipping, unreadable labels or non-functional primary controls.
