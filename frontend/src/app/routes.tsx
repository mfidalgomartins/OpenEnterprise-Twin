import { Route, Switch } from "wouter";
import type { ReactNode } from "react";

import {
  AdaptivePolicyPage,
  CalibrationStudioPage,
  DecisionLedgerPage,
  MonitoringCenterPage,
  OptimizationLabPage,
} from "../features/autopilot/AutopilotPages";
import "../features/autopilot/autopilot.css";
import {
  BriefingPage,
  DecisionsPage,
  NotFoundPage,
  ReportsPage,
  TwinPage,
} from "../features/control/ControlTowerPages";
import { ExecutiveReportPage } from "../features/reports/ExecutiveReportPage";
import { AccessDenied } from "../features/auth/AccessDenied";
import { CallbackPage } from "../features/auth/CallbackPage";
import { useAuth, type Role } from "../features/auth/authContext";
import { ScenarioComparePage } from "../features/scenarios/ScenarioComparePage";
import { ScenarioBuilder } from "../features/scenarios/ScenarioBuilder";
import { AppShell } from "./AppShell";

export function AppRoutes() {
  const { status } = useAuth();
  if (window.location.pathname === "/auth/callback") {
    return <CallbackPage />;
  }
  if (status === "loading") {
    return <AuthState title="Establishing secure session" />;
  }
  if (status !== "authenticated") {
    return <SignInPage />;
  }
  return (
    <AppShell>
      <Switch>
        <Route path="/" component={BriefingPage} />
        <Route path="/twin" component={TwinPage} />
        <Route path="/scenarios">
          <RequireRole roles={["analyst", "admin"]}>
            <ScenarioBuilder />
          </RequireRole>
        </Route>
        <Route
          path="/scenarios/:scenarioId/compare"
          component={ScenarioComparePage}
        />
        <Route path="/decisions" component={DecisionsPage} />
        <Route path="/calibration">
          <RequireRole roles={["analyst", "admin"]}>
            <CalibrationStudioPage />
          </RequireRole>
        </Route>
        <Route path="/optimization">
          <RequireRole roles={["analyst", "admin"]}>
            <OptimizationLabPage />
          </RequireRole>
        </Route>
        <Route path="/adaptive">
          <RequireRole roles={["analyst", "admin"]}>
            <AdaptivePolicyPage />
          </RequireRole>
        </Route>
        <Route path="/ledger">
          <RequireRole roles={["analyst", "approver", "admin"]}>
            <DecisionLedgerPage />
          </RequireRole>
        </Route>
        <Route path="/monitoring" component={MonitoringCenterPage} />
        <Route path="/reports/:experimentId" component={ExecutiveReportPage} />
        <Route path="/reports" component={ReportsPage} />
        <Route component={NotFoundPage} />
      </Switch>
    </AppShell>
  );
}

function RequireRole({
  children,
  roles,
}: {
  children: ReactNode;
  roles: Role[];
}) {
  const { can } = useAuth();
  return can(...roles) ? children : <AccessDenied />;
}

function AuthState({ title }: { title: string }) {
  return (
    <main className="auth-page" aria-live="polite">
      <div className="auth-panel">
        <p className="auth-panel__brand">OpenEnterprise Twin</p>
        <h1>{title}</h1>
        <p>Resolving effective tenant and role permissions.</p>
      </div>
    </main>
  );
}

function SignInPage() {
  const { error, login, mode, status } = useAuth();
  return (
    <main className="auth-page">
      <div className="auth-panel">
        <p className="auth-panel__brand">OpenEnterprise Twin</p>
        <h1>Enter the decision cockpit</h1>
        <p>
          Authenticate to load your effective tenant, role and governed
          workspaces.
        </p>
        {error ? <p className="auth-panel__error">{error}</p> : null}
        {mode === "oidc" ? (
          <button
            className="auth-action"
            onClick={() => void login()}
            type="button"
          >
            Sign in securely
          </button>
        ) : (
          <button
            className="auth-action"
            onClick={() => window.location.reload()}
            type="button"
          >
            Retry session
          </button>
        )}
        {status === "error" ? (
          <p className="auth-panel__hint">
            Check the deployment identity configuration if this persists.
          </p>
        ) : null}
      </div>
    </main>
  );
}
