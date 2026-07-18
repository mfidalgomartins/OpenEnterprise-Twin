import type { PropsWithChildren } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { BrandMark } from "../components/BrandMark";

const destinations = [
  { label: "Briefing", to: "/" },
  { label: "Twin", to: "/twin" },
  { label: "Scenarios", to: "/scenarios" },
  { label: "Decisions", to: "/decisions" },
  { label: "Reports", to: "/reports" },
] as const;

function ModelContext() {
  return (
    <div className="model-context">
      <p className="model-context__company">Northstar Components</p>
      <dl className="model-context__metadata">
        <div className="model-context__item">
          <dt>Reporting date</dt>
          <dd>May 16, 2025</dd>
        </div>
        <div className="model-context__item">
          <dt>Currency</dt>
          <dd>EUR</dd>
        </div>
        <div className="model-context__item">
          <dt>Model version</dt>
          <dd>Model v0.1</dd>
        </div>
        <div className="model-context__item">
          <dt>Data freshness</dt>
          <dd>Fresh 2h ago</dd>
        </div>
      </dl>
    </div>
  );
}

export function AppShell({ children }: PropsWithChildren) {
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
      <main className="app-main" id="main-content" tabIndex={-1}>
        {children ?? <Outlet />}
      </main>
    </div>
  );
}
