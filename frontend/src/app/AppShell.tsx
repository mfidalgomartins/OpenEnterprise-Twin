import { useEffect, useRef, useState, type PropsWithChildren } from "react";
import { Link, useLocation } from "wouter";

import { BrandMark } from "../components/BrandMark";
import { useAuth, type Role } from "../features/auth/authContext";
import { getCompanyReference } from "../features/scenarios/api";
import type { CompanyReference } from "../features/scenarios/types";

const destinations: ReadonlyArray<{
  label: string;
  to: string;
  roles?: readonly Role[];
}> = [
  { label: "Briefing", to: "/" },
  { label: "Twin", to: "/twin" },
  { label: "Scenarios", to: "/scenarios", roles: ["analyst", "admin"] },
  { label: "Calibration", to: "/calibration", roles: ["analyst", "admin"] },
  { label: "Optimization", to: "/optimization", roles: ["analyst", "admin"] },
  { label: "Adaptive", to: "/adaptive", roles: ["analyst", "admin"] },
  {
    label: "Ledger",
    to: "/ledger",
    roles: ["analyst", "approver", "admin"],
  },
  { label: "Monitoring", to: "/monitoring" },
  { label: "Jobs", to: "/jobs" },
  { label: "Decisions", to: "/decisions" },
  { label: "Reports", to: "/reports" },
] as const;

function ModelContext() {
  const { mode, session, logout } = useAuth();
  const [company, setCompany] = useState<CompanyReference | null>(null);

  useEffect(() => {
    let active = true;
    void getCompanyReference()
      .then((reference) => {
        if (active) {
          setCompany(reference);
        }
      })
      .catch(() => {
        if (active) {
          setCompany(null);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="model-context">
      <p className="model-context__company">
        {company?.name ?? "Reference model"}
      </p>
      <dl className="model-context__metadata">
        <div className="model-context__item">
          <dt>Currency</dt>
          <dd>EUR</dd>
        </div>
        <div className="model-context__item">
          <dt>Model version</dt>
          <dd>{company ? `v${company.model_version}` : "Loading"}</dd>
        </div>
        <div className="model-context__item">
          <dt>Data mode</dt>
          <dd>Synthetic reference</dd>
        </div>
        <div className="model-context__item">
          <dt>Tenant</dt>
          <dd>{session?.tenant_id ?? "—"}</dd>
        </div>
        <div className="model-context__item">
          <dt>Role</dt>
          <dd>{session?.roles.join(" · ") ?? "—"}</dd>
        </div>
      </dl>
      {mode === "oidc" ? (
        <button
          className="session-logout"
          onClick={() => void logout()}
          type="button"
        >
          Sign out
        </button>
      ) : null}
    </div>
  );
}

export function AppShell({ children }: PropsWithChildren) {
  const { can } = useAuth();
  const [location] = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const pathname = location;

  useEffect(() => {
    const titleByPath: Record<string, string> = {
      "/": "Decision briefing",
      "/decisions": "Decision portfolio",
      "/reports": "Decision briefs",
      "/scenarios": "Policy studio",
      "/twin": "Company twin",
      "/calibration": "Calibration studio",
      "/optimization": "Optimization lab",
      "/adaptive": "Adaptive policy builder",
      "/ledger": "Decision ledger",
      "/monitoring": "Monitoring center",
    };
    const title =
      titleByPath[pathname] ??
      (pathname.startsWith("/reports/")
        ? "Executive brief"
        : pathname.includes("/compare")
          ? "Decision room"
          : "OpenEnterprise Twin");
    document.title = `${title} · OpenEnterprise Twin`;
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="app-header">
        <div className="app-header__inner">
          <BrandMark />
          <nav aria-label="Primary navigation" className="primary-nav">
            <ul className="primary-nav__list">
              {destinations
                .filter(({ roles }) => !roles || can(...roles))
                .map(({ label, to }) => {
                const isActive =
                  to === "/"
                    ? pathname === "/"
                    : pathname === to || pathname.startsWith(`${to}/`);

                return (
                  <li key={to} className="primary-nav__item">
                    <Link
                      aria-current={isActive ? "page" : undefined}
                      className={`primary-nav__link${isActive ? " primary-nav__link--active" : ""}`}
                      href={to}
                    >
                      {label}
                    </Link>
                  </li>
                );
                })}
            </ul>
          </nav>
          <ModelContext />
        </div>
      </header>
      <main
        className="app-main"
        id="main-content"
        ref={mainRef}
        tabIndex={-1}
      >
        {children}
      </main>
    </div>
  );
}
