# Security policy

## Supported version

Security fixes target the latest release on `main`. Older tags are reference snapshots and do not receive backports.

## Report a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's **Security → Report a vulnerability** flow so the report, reproduction steps and proposed remediation remain private until a fix is available.

Include:

- the affected endpoint, component and version or commit;
- a minimal reproduction and expected impact;
- whether credentials, personal data or deployment secrets may be exposed;
- any safe mitigation already tested.

You should receive an acknowledgement within five working days. Valid reports are triaged by severity, fixed on a private branch and disclosed with the release when appropriate.

## Deployment boundary

OpenEnterprise Twin is a single-tenant reference system. Production mode requires an operator-supplied API key, but enterprise deployments still need their own identity, authorization, secret management, network controls, durable storage and monitoring. See the [threat model](docs/OpenEnterprise-Twin-threat-model.md) before deployment.
