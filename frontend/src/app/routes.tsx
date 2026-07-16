import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./AppShell";

interface RouteIntroProps {
  description: string;
  title: string;
}

function RouteIntro({ description, title }: RouteIntroProps) {
  return (
    <section aria-labelledby="route-title" className="route-intro">
      <h1 id="route-title">{title}</h1>
      <p>{description}</p>
    </section>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route
          index
          element={
            <RouteIntro
              description="Current posture, material constraints, and decisions awaiting review."
              title="Briefing"
            />
          }
        />
        <Route
          path="twin"
          element={
            <RouteIntro
              description="Company model, operating assumptions, and causal structure."
              title="Twin"
            />
          }
        />
        <Route
          path="scenarios"
          element={
            <RouteIntro
              description="Policy changes, experiment status, and comparable outcomes."
              title="Scenarios"
            />
          }
        />
        <Route
          path="decisions"
          element={
            <RouteIntro
              description="Recommendations, guardrails, owners, and review dates."
              title="Decisions"
            />
          }
        />
        <Route
          path="reports"
          element={
            <RouteIntro
              description="Published decision briefs and complete reproducibility records."
              title="Reports"
            />
          }
        />
        <Route path="*" element={<Navigate replace to="/" />} />
      </Route>
    </Routes>
  );
}
