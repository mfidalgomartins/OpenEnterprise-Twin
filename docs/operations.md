# Operator runbook

This runbook is the v0.5 operating contract for a controlled, single-tenant
OpenEnterprise Twin deployment. Commands assume the repository root unless a
different directory is shown.

## Current operating boundary

v0.5 is a single-node operating baseline:

- one FastAPI process owns a bounded in-process experiment runner;
- queued/running experiment state is persisted in PostgreSQL and recovered on
  process startup, but there is no distributed queue or worker lease;
- detailed artifacts live in one content-addressed filesystem directory;
- operational metrics are process-local and reset when the API restarts;
- one API key represents the deployment, not an end-user identity.

Run one API worker against one local artifact directory. Horizontal workers,
durable distributed jobs, OIDC/RBAC, tenant isolation and object storage are
later roadmap phases.

## Secure configuration

Create an untracked runtime file and replace every placeholder:

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

Use the generated value for `POSTGRES_PASSWORD` and the matching password
segment in `OPENENTERPRISE_TWIN_DATABASE_URL`. Generate a separate API key of
at least 32 characters for production. Supply production secrets through the
deployment secret manager; never commit `.env`, print secrets in logs or expose
the API key to browser code.

Production must set:

```bash
OPENENTERPRISE_TWIN_DEPLOYMENT_ENVIRONMENT=production
OPENENTERPRISE_TWIN_API_KEY=<high-entropy-secret-loaded-from-secret-manager>
OPENENTERPRISE_TWIN_TRUSTED_HOSTS='["twin.example.com"]'
OPENENTERPRISE_TWIN_DATABASE_URL=<private-postgresql-url>
OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY=/var/lib/openenterprise-twin/artifacts
OPENENTERPRISE_TWIN_BUILD_COMMIT=<lowercase-git-commit>
```

The application fails closed if the production API key is shorter than 32
characters or trusted hosts are absent, wildcarded or left at development
defaults.

## Startup order

For a clean local evaluation:

```bash
make install
make db
make migrate
make seed
make dev
```

`make dev` already performs install, database startup, migration and seeding;
the expanded sequence above makes the dependency order explicit. For daily
local use, `cp .env.example .env`, set the database password in both required
values, then run only `make dev`.

For a controlled deployment:

1. Load secrets and validated environment settings.
2. Start PostgreSQL and wait for `pg_isready`.
3. Take a coherent database and artifact backup before an upgrade.
4. Apply migrations once, before starting the API.
5. Start exactly one API worker for the local filesystem adapter.
6. Pass liveness and readiness checks.
7. Route traffic through the same-origin TLS ingress.

The migration and API commands are:

```bash
cd backend
../.venv/bin/python -m alembic upgrade head
exec ../.venv/bin/python -m uvicorn \
  openenterprise_twin.api.app:create_app \
  --factory \
  --host 127.0.0.1 \
  --port 8000 \
  --workers 1
```

Bind to `0.0.0.0` only inside a private container network. Expose the Nginx/TLS
edge, not the backend port, to untrusted networks.

## Migrations

Inspect and apply the migration graph:

```bash
cd backend
../.venv/bin/python -m alembic current
../.venv/bin/python -m alembic heads
../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m alembic current
```

The expected v0.5 head is `0003_decision_loop`. CI validates
`upgrade head → downgrade base → upgrade head` against PostgreSQL. Do not run a
downgrade while the API is accepting traffic. A one-revision downgrade for a
tested rollback is:

```bash
cd backend
../.venv/bin/python -m alembic downgrade -1
```

Take a backup first and pair schema rollback with a compatible application
release. v0.5 adds no migration beyond the existing `0003_decision_loop` head.

## Liveness, readiness and system evidence

Liveness proves only that the process can answer HTTP:

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

Readiness verifies PostgreSQL with `SELECT 1` and verifies artifact storage with
an exclusive temporary write, flush, `fsync`, exact read and cleanup:

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:8000/ready
```

Expected:

```json
{"status":"ready","checks":{"artifacts":"ready","database":"ready"}}
```

A failed dependency returns `503` with the stable RFC 9457 code
`service_not_ready`; it does not disclose a database URL, filesystem path or
exception. Restart a process that fails liveness. Investigate its dependencies
when liveness passes but readiness fails.

Build information and metrics require the normal API principal. Load the API
key into the shell from the secret manager, then run:

```bash
curl --fail --silent --show-error \
  --header "X-API-Key: ${OPENENTERPRISE_TWIN_API_KEY}" \
  http://127.0.0.1:8000/api/v1/system/info

curl --fail --silent --show-error \
  --header "X-API-Key: ${OPENENTERPRISE_TWIN_API_KEY}" \
  http://127.0.0.1:8000/api/v1/system/metrics
```

The metrics response contains process uptime and aggregate HTTP count/duration.
Its only labels are HTTP method, registered route template and status family.
It contains no key, principal, tenant, scenario, trace identifier or payload.

## Backup

Database rows and artifact digests form one evidence set. For a coherent
single-node backup, stop writes or drain the API before capturing both.

```bash
backup_dir="outputs/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${backup_dir}"

docker compose exec -T db pg_dump \
  --username=openenterprise_twin \
  --dbname=openenterprise_twin \
  --format=custom \
  --no-owner \
  --file=/tmp/openenterprise-twin.dump
docker compose cp \
  db:/tmp/openenterprise-twin.dump \
  "${backup_dir}/postgres.dump"
docker compose exec -T db rm -f /tmp/openenterprise-twin.dump

artifact_dir="${OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY:-artifacts}"
tar --create --gzip \
  --file="${backup_dir}/artifacts.tar.gz" \
  --directory="$(dirname "${artifact_dir}")" \
  "$(basename "${artifact_dir}")"

shasum -a 256 "${backup_dir}/postgres.dump" \
  "${backup_dir}/artifacts.tar.gz" \
  > "${backup_dir}/SHA256SUMS"
```

Store the backup and checksum outside the application host with encryption,
access control and tested retention. A database dump without its corresponding
artifacts cannot reconstruct complete experiment evidence.

## Restore

Restore into a new database and a new artifact root; do not overwrite the live
pair. Validate that isolated pair with a candidate API process, then change both
service settings together while the public API is stopped. A running deployment
therefore observes either the complete old pair or the complete restored pair.

```bash
set -euo pipefail
backup_dir="outputs/backups/<timestamp>"
shasum -a 256 --check "${backup_dir}/SHA256SUMS"

configured_artifact_dir="${OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY:?set an absolute artifact directory}"
while test "${configured_artifact_dir}" != "/" &&
  test "${configured_artifact_dir%/}" != "${configured_artifact_dir}"; do
  configured_artifact_dir="${configured_artifact_dir%/}"
done
case "${configured_artifact_dir}" in
  /*) ;;
  *) echo "Artifact directory must be absolute" >&2; exit 1 ;;
esac
test "${configured_artifact_dir}" != "/" || {
  echo "Refusing filesystem root" >&2
  exit 1
}
test ! -L "${configured_artifact_dir}" || {
  echo "Refusing a symlinked artifact directory" >&2
  exit 1
}

configured_parent="$(dirname "${configured_artifact_dir}")"
artifact_name="$(basename "${configured_artifact_dir}")"
case "${artifact_name}" in
  ""|"."|"..") echo "Refusing unsafe artifact basename" >&2; exit 1 ;;
esac
test -d "${configured_parent}" && test ! -L "${configured_parent}" || {
  echo "Artifact parent must be an existing real directory" >&2
  exit 1
}
artifact_parent="$(cd "${configured_parent}" && pwd -P)"
artifact_dir="${artifact_parent}/${artifact_name}"
test ! -L "${artifact_dir}" || {
  echo "Refusing a symlinked canonical artifact directory" >&2
  exit 1
}
test ! -e "${artifact_dir}" || test -d "${artifact_dir}" || {
  echo "Artifact target exists but is not a directory" >&2
  exit 1
}
restore_id="$(date -u +%Y%m%d%H%M%S)"
restore_db="openenterprise_twin_restore_${restore_id}"
restore_stage="$(mktemp -d "${artifact_parent}/.artifact-restore.XXXXXX")"
restored_artifacts="${artifact_parent}/${artifact_name}.restore.${restore_id}"
test ! -e "${restored_artifacts}" || {
  echo "Restore artifact root already exists" >&2
  exit 1
}

restore_complete=0
cleanup_incomplete_restore() {
  if test "${restore_complete}" != "1"; then
    docker compose exec -T db dropdb \
      --if-exists \
      --username=openenterprise_twin \
      "${restore_db}" >/dev/null 2>&1 || true
    rm -rf "${restore_stage}" "${restored_artifacts}"
  fi
}
trap cleanup_incomplete_restore EXIT
archive="${backup_dir}/artifacts.tar.gz"

.venv/bin/python - "${archive}" "${restore_stage}" "${artifact_name}" <<'PY'
from pathlib import Path, PurePosixPath
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
artifact_name = sys.argv[3]

with tarfile.open(archive, mode="r:gz") as bundle:
    for member in bundle.getmembers():
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != artifact_name
            or member.issym()
            or member.islnk()
            or member.isdev()
            or member.isfifo()
        ):
            raise SystemExit(f"unsafe artifact member: {member.name!r}")
    bundle.extractall(destination, filter="data")
PY

staged_artifacts="${restore_stage}/${artifact_name}"
test -d "${staged_artifacts}" || {
  echo "Archive does not contain the expected artifact root" >&2
  exit 1
}
mv "${staged_artifacts}" "${restored_artifacts}"

docker compose exec -T db createdb \
  --username=openenterprise_twin \
  "${restore_db}"
docker compose exec -T db pg_restore \
  --username=openenterprise_twin \
  --dbname="${restore_db}" \
  --single-transaction \
  --exit-on-error \
  --no-owner \
  --no-privileges \
  < "${backup_dir}/postgres.dump"

restore_complete=1
rm -rf "${restore_stage}"
trap - EXIT

printf 'Restored database: %s\nRestored artifacts: %s\n' \
  "${restore_db}" \
  "${restored_artifacts}"
```

The script rejects archive traversal, links and special files; a failure drops
the isolated restore database and removes only generated staging paths. It
never mutates the live database or artifact root.

Construct a secret-managed `RESTORE_DATABASE_URL` ending in the printed restore
database name. Migrate and validate the restored pair without public traffic:

```bash
set -euo pipefail
RESTORE_DATABASE_URL="${RESTORE_DATABASE_URL:?set the isolated restore database URL}"
RESTORE_ARTIFACT_DIRECTORY="${RESTORE_ARTIFACT_DIRECTORY:?set the printed restore artifact root}"

(cd backend &&
  OPENENTERPRISE_TWIN_DATABASE_URL="${RESTORE_DATABASE_URL}" \
  OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY="${RESTORE_ARTIFACT_DIRECTORY}" \
  ../.venv/bin/python -m alembic upgrade head)

OPENENTERPRISE_TWIN_DATABASE_URL="${RESTORE_DATABASE_URL}" \
OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY="${RESTORE_ARTIFACT_DIRECTORY}" \
  .venv/bin/python -m uvicorn openenterprise_twin.api.app:create_app \
    --factory --host 127.0.0.1 --port 18001 &
candidate_pid=$!
trap 'kill "${candidate_pid}" 2>/dev/null || true' EXIT

for attempt in $(seq 1 30); do
  curl --fail --silent http://127.0.0.1:18001/ready && break
  test "${attempt}" != "30" || exit 1
  sleep 1
done
curl --fail --silent \
  http://127.0.0.1:18001/api/v1/scenarios/current-plan \
  --header "X-API-Key: ${OPENENTERPRISE_TWIN_API_KEY:-}"
kill -TERM "${candidate_pid}"
wait "${candidate_pid}"
trap - EXIT
```

Run the release-appropriate evidence/digest smoke checks before cutover. Then
stop the live API and atomically replace one service-manager environment file
containing both `OPENENTERPRISE_TWIN_DATABASE_URL` and
`OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY`. Start one API process from that
versioned configuration and require `/health` and `/ready` before restoring
traffic. Keep the old database and artifact root through the rollback retention
window; rollback selects both old values in the same configuration revision.
Do not merge artifacts from unrelated backups or update the two settings
independently.

## Graceful shutdown

Send `SIGTERM` and allow the FastAPI lifespan handler to drain bounded work:

```bash
kill -TERM "${API_PID}"
wait "${API_PID}"
```

The runner waits up to
`OPENENTERPRISE_TWIN_EXPERIMENT_SHUTDOWN_TIMEOUT_SECONDS`, cancels work that has
not started and disposes database connections. Configure the service manager or
container termination grace period above that timeout. An interrupted
`running` experiment is recovered to the persisted queue when the next process
starts; the current adapter does not move execution to another node.

## Dependency and release audits

Run audits from a clean locked install:

```bash
make install

cd frontend
npm audit --audit-level=high
cd ..

.venv/bin/python -m pip install "pip-audit==2.10.1"
.venv/bin/python -m pip_audit \
  --require-hashes \
  --requirement backend/requirements.lock

make lint
make test
make build
make docker-build
make e2e
```

Treat a high or critical locked-dependency finding, failed migration gate or
failed readiness check as a release blocker.

## Incident triage

Capture timestamps and the deployed build commit without copying secrets or
business payloads into the incident record.

1. Check `/health`. If it fails, inspect process/container exit status and
   application logs, then restart under the service manager.
2. If `/health` passes, check `/ready`. A `503` means PostgreSQL or the artifact
   directory is unavailable.
3. Check PostgreSQL:

   ```bash
   docker compose exec -T db pg_isready \
     --username=openenterprise_twin \
     --dbname=openenterprise_twin
   ```

4. Check artifact capacity and permissions:

   ```bash
   artifact_dir="${OPENENTERPRISE_TWIN_ARTIFACT_DIRECTORY:-artifacts}"
   test -d "${artifact_dir}" &&
     test -r "${artifact_dir}" &&
     test -w "${artifact_dir}"
   df -h "${artifact_dir}"
   ```

5. Retrieve protected `/api/v1/system/info` and
   `/api/v1/system/metrics`. Compare 4xx/5xx families and registered route
   templates; remember that counters reset after restart.
6. For repeated `401`, `413`, budget `422` or `429` responses, preserve
   payload-free ingress/audit evidence, confirm rate and size controls, and
   rotate the API key if exposure is suspected.
7. If artifact digest validation fails, stop decision publication, preserve the
   database and artifact directory, and restore a verified matching backup.

## Rollback

Prefer rolling back the immutable application image or release artifact while
keeping the evidence store intact. v0.5 does not introduce a database revision,
so an application rollback to v0.4.1 keeps schema head
`0003_decision_loop`.

1. Remove the instance from traffic and stop the API gracefully.
2. Preserve logs and take a coherent database/artifact backup.
3. Deploy the previously verified v0.4.1 image or build artifact.
4. Do not downgrade PostgreSQL for a v0.5 → v0.4.1 rollback.
5. Start one worker, pass `/health` and the release-appropriate dependency
   checks, then restore traffic gradually.
6. If data integrity is in doubt, restore the matching database and artifact
   backup instead of replaying writes manually.

Record the failed build commit, rollback release, migration head, backup
identifier and probe results in the incident timeline.
