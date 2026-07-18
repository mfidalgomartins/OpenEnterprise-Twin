import { useParams, useSearchParams } from "react-router-dom";

import { DecisionHeader } from "./DecisionHeader";
import { DecisionRail } from "./DecisionRail";
import { EvidenceSection } from "./EvidenceSection";
import { MechanismSection } from "./MechanismSection";
import { OutcomeSummary } from "./OutcomeSummary";
import { OutcomeTrajectory } from "./OutcomeTrajectory";
import { SensitivitySection } from "./SensitivitySection";
import { useScenarioDecisionRoom } from "./useScenarioDecisionRoom";
import "./scenario-compare.css";

function RecalculationStatus({
  hasEvidence,
  isFetching,
}: {
  hasEvidence: boolean;
  isFetching: boolean;
}) {
  let message = "Scenario evidence is current";

  if (isFetching && hasEvidence) {
    message =
      "Recalculating scenario evidence. Previous evidence remains visible";
  } else if (isFetching) {
    message = "Recalculating scenario evidence";
  }

  return (
    <p
      aria-live="polite"
      aria-atomic="true"
      className="decision-room__live-status"
      role="status"
    >
      {message}
    </p>
  );
}

export function ScenarioComparePage() {
  const { scenarioId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const experimentId = searchParams.get("experiment")?.trim() ?? "";
  const { comparison, error, isFetching, isPending, report } =
    useScenarioDecisionRoom(experimentId);
  const hasEvidence = Boolean(comparison && report);

  if (!experimentId) {
    return (
      <section className="decision-room-state">
        <RecalculationStatus hasEvidence={false} isFetching={false} />
        <h1>Scenario comparison unavailable</h1>
        <p role="alert">
          Add an experiment query parameter to load decision evidence.
        </p>
      </section>
    );
  }

  if (isPending && !hasEvidence) {
    return (
      <section
        aria-labelledby="decision-room-loading-title"
        className="decision-room-state"
      >
        <RecalculationStatus hasEvidence={false} isFetching={isFetching} />
        <h1 id="decision-room-loading-title">Preparing the decision room</h1>
        <p>Loading paired comparison and executive report evidence.</p>
      </section>
    );
  }

  if (error || !comparison || !report) {
    return (
      <section className="decision-room-state">
        <RecalculationStatus hasEvidence={false} isFetching={false} />
        <h1>Scenario comparison unavailable</h1>
        <p role="alert">
          The comparison evidence could not be loaded. Check the experiment
          identifier and try again.
        </p>
      </section>
    );
  }

  if (scenarioId && comparison.candidate_scenario_id !== scenarioId) {
    return (
      <section className="decision-room-state">
        <RecalculationStatus hasEvidence isFetching={false} />
        <h1>Scenario and experiment do not match</h1>
        <p role="alert">
          This experiment belongs to {comparison.candidate_scenario_name}.
          Open the comparison from that scenario.
        </p>
      </section>
    );
  }

  return (
    <article className="decision-room">
      <RecalculationStatus hasEvidence isFetching={isFetching} />
      <div className="decision-room__layout">
        <DecisionHeader comparison={comparison} report={report} />
        <DecisionRail report={report} />
        <OutcomeSummary outcomes={report.outcome_deltas} />
        <div className="decision-room__evidence">
          <section
            aria-labelledby="impact-title"
            className="decision-chapter decision-chapter--impact"
          >
            <div className="decision-chapter__heading">
              <h2 id="impact-title">Impact</h2>
              <p>
                Baseline and candidate means remain connected to their exact
                replication uncertainty.
              </p>
            </div>
            <OutcomeTrajectory comparison={comparison} />
          </section>
          <MechanismSection mechanisms={report.mechanisms} />
          <SensitivitySection metrics={comparison.metric_results} />
          <EvidenceSection experimentId={experimentId} report={report} />
        </div>
      </div>
    </article>
  );
}
