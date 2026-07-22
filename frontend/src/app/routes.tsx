import { Route, Routes } from "react-router-dom";

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
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<BriefingPage />} />
        <Route path="twin" element={<TwinPage />} />
        <Route
          path="scenarios"
          element={<ScenarioBuilder />}
        />
        <Route
          path="scenarios/:scenarioId/compare"
          element={<ScenarioComparePage />}
        />
        <Route path="decisions" element={<DecisionsPage />} />
        <Route
          path="reports/:experimentId"
          element={<ExecutiveReportPage />}
        />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
