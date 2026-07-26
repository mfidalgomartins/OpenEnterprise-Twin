import { Route, Switch } from "wouter";

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
import { ScenarioComparePage } from "../features/scenarios/ScenarioComparePage";
import { ScenarioBuilder } from "../features/scenarios/ScenarioBuilder";
import { AppShell } from "./AppShell";

export function AppRoutes() {
  return (
    <AppShell>
      <Switch>
        <Route path="/" component={BriefingPage} />
        <Route path="/twin" component={TwinPage} />
        <Route path="/scenarios" component={ScenarioBuilder} />
        <Route
          path="/scenarios/:scenarioId/compare"
          component={ScenarioComparePage}
        />
        <Route path="/decisions" component={DecisionsPage} />
        <Route path="/calibration" component={CalibrationStudioPage} />
        <Route path="/optimization" component={OptimizationLabPage} />
        <Route path="/adaptive" component={AdaptivePolicyPage} />
        <Route path="/ledger" component={DecisionLedgerPage} />
        <Route path="/monitoring" component={MonitoringCenterPage} />
        <Route path="/reports/:experimentId" component={ExecutiveReportPage} />
        <Route path="/reports" component={ReportsPage} />
        <Route component={NotFoundPage} />
      </Switch>
    </AppShell>
  );
}
