import type { MechanismNarrative } from "./types";

interface MechanismSectionProps {
  mechanisms: MechanismNarrative[];
}

export function MechanismSection({ mechanisms }: MechanismSectionProps) {
  return (
    <section aria-labelledby="mechanism-title" className="decision-chapter">
      <div className="decision-chapter__heading">
        <h2 id="mechanism-title">Mechanism</h2>
        <p>
          The recommendation follows the configured levers in the order the
          model applies them.
        </p>
      </div>
      {mechanisms.length > 0 ? (
        <ol className="mechanism-chain">
          {mechanisms.map((mechanism) => (
            <li key={mechanism.mechanism_id}>
              <h3>{mechanism.title}</h3>
              <p>{mechanism.detail}</p>
            </li>
          ))}
        </ol>
      ) : (
        <p>No changed policy lever was recorded for this scenario.</p>
      )}
    </section>
  );
}
