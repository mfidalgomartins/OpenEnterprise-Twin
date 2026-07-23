import { useEffect, useRef, useState, type PropsWithChildren } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { BrandMark } from "../components/BrandMark";
import { getCompanyReference } from "../features/scenarios/api";
import type { CompanyReference } from "../features/scenarios/types";

const destinations = [
  { label: "Briefing", to: "/" },
  { label: "Twin", to: "/twin" },
  { label: "Scenarios", to: "/scenarios" },
  { label: "Decisions", to: "/decisions" },
  { label: "Reports", to: "/reports" },
] as const;

function ModelContext() {
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
      </dl>
    </div>
  );
}

export function AppShell({ children }: PropsWithChildren) {
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const titleByPath: Record<string, string> = {
      "/": "Decision briefing",
      "/decisions": "Decision portfolio",
      "/reports": "Decision briefs",
      "/scenarios": "Policy studio",
      "/twin": "Company twin",
    };
    const title =
      titleByPath[location.pathname] ??
      (location.pathname.startsWith("/reports/")
        ? "Executive brief"
        : location.pathname.includes("/compare")
          ? "Decision room"
          : "OpenEnterprise Twin");
    document.title = `${title} · OpenEnterprise Twin`;
    mainRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

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
              {destinations.map(({ label, to }) => (
                <li key={to} className="primary-nav__item">
                  <NavLink
                    className={({ isActive }) =>
                      `primary-nav__link${isActive ? " primary-nav__link--active" : ""}`
                    }
                    end={to === "/"}
                    to={to}
                  >
                    {label}
                  </NavLink>
                </li>
              ))}
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
        {children ?? <Outlet />}
      </main>
    </div>
  );
}
