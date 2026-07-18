import { Link } from "react-router-dom";

import type {
  ExperimentPhase,
  LastCompletedExperiment,
  RunIssue,
} from "./useScenarioExperiment";

const phaseMessages: Record<ExperimentPhase, string> = {
  idle: "Ready to run a paired comparison.",
  saving_baseline: "Checking the versioned baseline scenario.",
  running_baseline: "Running baseline experiment with the selected seed.",
  saving_candidate: "Saving an immutable candidate scenario revision.",
  running_candidate: "Running candidate experiment with common random numbers.",
  completed: "Comparison evidence is ready.",
  failed: "The experiment stopped before new evidence was produced.",
};

interface ExperimentProgressProps {
  issue: RunIssue | null;
  lastCompleted: LastCompletedExperiment | null;
  phase: ExperimentPhase;
}

export function ExperimentProgress({
  issue,
  lastCompleted,
  phase,
}: ExperimentProgressProps) {
  return (
    <section aria-labelledby="experiment-progress-title" className="experiment-progress">
      <h2 id="experiment-progress-title">Experiment progress</h2>
      <div aria-atomic="true" aria-live="polite" role="status">
        <p>{phaseMessages[phase]}</p>
      </div>

      {issue ? (
        <div className="experiment-progress__error" role="alert">
          <strong>Error code: {issue.code}</strong>
          <p>{issue.detail}</p>
          <p>{issue.correctiveAction}</p>
        </div>
      ) : null}

      {lastCompleted ? (
        <div className="experiment-progress__latest">
          <h3>Latest completed comparison</h3>
          <p>
            Experiment {lastCompleted.experimentId} remains available while a
            new revision recalculates.
          </p>
          <Link
            to={`/scenarios/${encodeURIComponent(lastCompleted.scenarioId)}/compare?experiment=${lastCompleted.experimentId}`}
          >
            Open latest decision room
          </Link>
        </div>
      ) : null}
    </section>
  );
}
