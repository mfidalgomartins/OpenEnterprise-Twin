interface PolicyLeverProps {
  baseline: string;
  error?: string;
  id: string;
  label: string;
  maximum?: number;
  mechanism: string;
  minimum?: number;
  onChange: (value: string) => void;
  step?: number | "any";
  unit: string;
  value: string;
}

export function PolicyLever({
  baseline,
  error,
  id,
  label,
  maximum,
  mechanism,
  minimum,
  onChange,
  step = 1,
  unit,
  value,
}: PolicyLeverProps) {
  const descriptionId = `${id}-description`;
  const errorId = `${id}-error`;
  const changed = Number(value) !== 0;

  return (
    <div className={`policy-lever${changed ? " policy-lever--changed" : ""}`}>
      <div className="policy-lever__heading">
        <label htmlFor={id}>{label}</label>
        <span>{changed ? "Changed" : "Baseline"}</span>
      </div>
      <p id={descriptionId}>{mechanism}</p>
      <div className="policy-lever__control">
        <input
          aria-describedby={`${descriptionId}${error ? ` ${errorId}` : ""}`}
          aria-invalid={Boolean(error)}
          id={id}
          max={maximum}
          min={minimum}
          onChange={(event) => onChange(event.target.value)}
          step={step}
          type="number"
          value={value}
        />
        <span aria-hidden="true">{unit}</span>
      </div>
      <dl className="policy-lever__values">
        <div>
          <dt>Baseline</dt>
          <dd>{baseline}</dd>
        </div>
        <div>
          <dt>Changed value</dt>
          <dd>
            {value || "—"} {unit}
          </dd>
        </div>
      </dl>
      {error ? (
        <p className="policy-lever__error" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
