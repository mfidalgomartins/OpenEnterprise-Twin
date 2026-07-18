import { formatInteger } from "./formatScenario";
import type { BriefProvenance, ExecutiveBrief, PluginVersion } from "./types";

interface EvidenceSectionProps {
  experimentId: string;
  report: ExecutiveBrief;
}

interface ProvenanceItem {
  label: string;
  value: string;
  code?: boolean;
}

function provenanceItems(
  provenance: BriefProvenance,
  experimentId: string,
): ProvenanceItem[] {
  return [
    { label: "Experiment", value: experimentId, code: true },
    {
      label: "Comparison digest",
      value: provenance.comparison_digest,
      code: true,
    },
    {
      label: "Baseline experiment digest",
      value: provenance.baseline_experiment_digest,
      code: true,
    },
    {
      label: "Candidate experiment digest",
      value: provenance.candidate_experiment_digest,
      code: true,
    },
    { label: "Company model hash", value: provenance.company_model_hash, code: true },
    {
      label: "Baseline assumptions hash",
      value: provenance.baseline_resolved_assumptions_hash,
      code: true,
    },
    {
      label: "Candidate assumptions hash",
      value: provenance.candidate_resolved_assumptions_hash,
      code: true,
    },
    {
      label: "Baseline experiment created",
      value: provenance.baseline_experiment_created_at,
    },
    {
      label: "Candidate experiment created",
      value: provenance.candidate_experiment_created_at,
    },
    {
      label: "Baseline experiment duration",
      value: provenance.baseline_experiment_duration_seconds.toFixed(2) + " s",
    },
    {
      label: "Candidate experiment duration",
      value: provenance.candidate_experiment_duration_seconds.toFixed(2) + " s",
    },
    { label: "Comparison created", value: provenance.comparison_created_at },
    {
      label: "Comparison duration",
      value: provenance.comparison_duration_seconds.toFixed(2) + " s",
    },
    { label: "Report created", value: provenance.created_at },
    {
      label: "Report duration",
      value: provenance.duration_seconds.toFixed(2) + " s",
    },
  ];
}

function uniquePlugins(provenance: BriefProvenance): PluginVersion[] {
  const plugins = new Map<string, PluginVersion>();

  for (const plugin of [
    ...provenance.baseline_plugin_versions,
    ...provenance.candidate_plugin_versions,
  ]) {
    plugins.set(plugin.plugin_id + "@" + plugin.version, plugin);
  }

  return [...plugins.values()];
}

export function EvidenceSection({
  experimentId,
  report,
}: EvidenceSectionProps) {
  const { provenance } = report;
  const plugins = uniquePlugins(provenance);

  return (
    <section
      aria-labelledby="evidence-title"
      className="decision-chapter evidence-section"
      id="evidence"
    >
      <div className="decision-chapter__heading">
        <h2 id="evidence-title">Evidence</h2>
        <p>
          Complete model, seed, replication and assumption provenance for this
          recommendation.
        </p>
      </div>

      <div className="evidence-section__summary">
        <p>Model {provenance.company_model_version}</p>
        <p>Engine {provenance.engine_version}</p>
        <p>Scenario schema {provenance.scenario_schema_version}</p>
        <p>Shock tape {provenance.shock_tape_version}</p>
        <p>Seed {formatInteger(provenance.master_seed)}</p>
        <p>{formatInteger(provenance.replication_count)} paired replications</p>
      </div>

      <div className="evidence-section__columns">
        <div>
          <h3>Assumptions</h3>
          <ol className="evidence-section__assumptions">
            {report.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ol>
        </div>
        <div>
          <h3>Plugin versions</h3>
          {plugins.length > 0 ? (
            <ul className="evidence-section__plugins">
              {plugins.map((plugin) => (
                <li key={plugin.plugin_id + "@" + plugin.version}>
                  {plugin.plugin_id} {plugin.version}
                </li>
              ))}
            </ul>
          ) : (
            <p>No external plugin version was recorded.</p>
          )}
        </div>
      </div>

      <dl className="provenance-list">
        {provenanceItems(provenance, experimentId).map((item) => (
          <div key={item.label}>
            <dt>{item.label}</dt>
            <dd>{item.code ? <code>{item.value}</code> : item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
