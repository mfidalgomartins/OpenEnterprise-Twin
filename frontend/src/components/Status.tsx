import type { PropsWithChildren } from "react";

export interface StatusProps extends PropsWithChildren {
  tone?: "neutral" | "success" | "comparison" | "warning" | "risk";
}

export function Status({ children, tone = "neutral" }: StatusProps) {
  return (
    <span className={`status status--${tone}`}>
      <span aria-hidden="true" className="status__marker" />
      {children}
    </span>
  );
}
