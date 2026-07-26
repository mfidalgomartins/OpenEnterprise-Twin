# Task 6 report

## Result

- Upgraded every `actions/checkout`, `actions/setup-python`,
  `actions/setup-node` and `actions/upload-artifact` reference in the CI and
  Security workflows to `@v7`.
- Preserved the existing stable job names used by the planned branch
  protection rules.
- Added PostgreSQL integration runtime assertions for:
  - exact dependency-free `/health` liveness contract;
  - exact `/ready` database and artifact-storage contract;
  - unauthenticated `401` on `/api/v1/system/info`;
  - authenticated, allowlisted system metadata with valid package version,
    build SHA and bounded capability names;
  - absence of the CI API key, database URL and artifact path from metadata.
- Configured a clearly non-secret, CI-only API key longer than 32 characters
  and `${{ github.sha }}` as the validated build identifier. The deployment
  environment remains unchanged.
- Retained CodeQL for Python and JavaScript/TypeScript plus the hash-pinned
  Python and locked npm dependency audits.
- Reworked the PR template around concise governance impact and reproducible
  evidence.

## Changed files

- `.github/workflows/ci.yml`
- `.github/workflows/security.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.superpowers/sdd/task-6-report.md`

No GitHub settings were changed and PR #2 was not modified.

## Validation

- PyYAML parsed both workflows: 5 CI jobs and 2 Security jobs.
- `bash -n` accepted all 17 workflow `run` blocks.
- Python compiled the embedded runtime-contract heredoc.
- Static reference validation found only:
  - `actions/checkout@v7` (7 references);
  - `actions/setup-python@v7` (4 references);
  - `actions/setup-node@v7` (3 references);
  - `actions/upload-artifact@v7` (4 references).
- Static security validation confirmed both CodeQL languages and both locked
  audit commands remain present.
- The exact embedded runtime probe passed locally against a temporary seeded
  application instance: liveness, readiness, unauthenticated/authenticated
  metadata and current-scenario checks all passed. PostgreSQL service and
  reversible migration execution remain enforced by the CI job.
- `git diff --check` passed for the scoped files.

The final commit hash is recorded in the parent task output because writing a
commit hash into the commit that creates it would be self-referential.
