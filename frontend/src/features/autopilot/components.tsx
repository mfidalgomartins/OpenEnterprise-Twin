import type { PropsWithChildren, ReactNode } from "react";

export function Panel({
  title,
  description,
  actions,
  children,
}: PropsWithChildren<{
  title: string;
  description?: string;
  actions?: ReactNode;
}>) {
  return (
    <section className="ap-panel" aria-label={title}>
      <header className="ap-panel__head">
        <div>
          <h2 className="ap-panel__title">{title}</h2>
          {description ? (
            <p className="ap-panel__description">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="ap-panel__actions">{actions}</div> : null}
      </header>
      <div className="ap-panel__body">{children}</div>
    </section>
  );
}

export function StateBanner({
  kind,
  title,
  detail,
  action,
}: {
  kind: "loading" | "empty" | "error";
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className={`ap-state ap-state--${kind}`} role="status">
      <p className="ap-state__title">{title}</p>
      {detail ? <p className="ap-state__detail">{detail}</p> : null}
      {action ? <div className="ap-state__action">{action}</div> : null}
    </div>
  );
}

export function ScoreDial({
  value,
  max,
  label,
  tone,
}: {
  value: number;
  max: number;
  label: string;
  tone: "high" | "medium" | "low";
}) {
  const fraction = Math.max(0, Math.min(1, value / max));
  return (
    <div className={`ap-dial ap-dial--${tone}`}>
      <div
        className="ap-dial__ring"
        style={{ ["--ap-fraction" as string]: fraction.toFixed(3) }}
        role="img"
        aria-label={`${label}: ${value.toFixed(1)} of ${max}`}
      >
        <span className="ap-dial__value">{value.toFixed(1)}</span>
      </div>
      <p className="ap-dial__label">{label}</p>
    </div>
  );
}

export function Meter({
  value,
  label,
  detail,
  tone = "decision",
}: {
  value: number;
  label: string;
  detail?: string;
  tone?: "decision" | "risk" | "warning";
}) {
  const fraction = Math.max(0, Math.min(1, value));
  return (
    <div className="ap-meter">
      <div className="ap-meter__head">
        <span className="ap-meter__label">{label}</span>
        <span className="ap-meter__value">{(fraction * 100).toFixed(0)}%</span>
      </div>
      <div className="ap-meter__track">
        <div
          className={`ap-meter__fill ap-meter__fill--${tone}`}
          style={{ width: `${(fraction * 100).toFixed(1)}%` }}
        />
      </div>
      {detail ? <p className="ap-meter__detail">{detail}</p> : null}
    </div>
  );
}

export function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  return (
    <div className="ap-stat">
      <dt className="ap-stat__label">{label}</dt>
      <dd className={`ap-stat__value ap-stat__value--${tone ?? "neutral"}`}>
        {value}
      </dd>
    </div>
  );
}

export function Badge({
  tone,
  children,
}: PropsWithChildren<{ tone: string }>) {
  return <span className={`ap-badge ap-badge--${tone}`}>{children}</span>;
}
