"""Seed Northstar and create the reproducible flagship Decision Room demo."""

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic, sleep

import httpx
from pydantic import BaseModel
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.api.schemas import (
    ExperimentCreate,
    ExperimentRead,
    ScenarioRead,
)
from openenterprise_twin.domain.scenario import (
    MaterialPolicyChange,
    PolicyLevers,
    ResourcePolicyChange,
    Scenario,
    SegmentPaymentTermChange,
    SegmentProductPriceChange,
)
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.models import Base
from openenterprise_twin.infrastructure.repositories import ScenarioRepository
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.reporting.brief import ExecutiveBrief
from openenterprise_twin.scenarios.comparison import ScenarioComparison
from openenterprise_twin.simulation.reference import build_baseline_scenario

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:5173"
DEFAULT_MASTER_SEED = 731
DEFAULT_REPLICATIONS = 100
DEFAULT_TIMEOUT_SECONDS = 600.0


class DemoError(RuntimeError):
    """Raised when a seed or demo run cannot produce auditable evidence."""


@dataclass(frozen=True, slots=True)
class DemoResult:
    """Identifiers required to reopen and reproduce the flagship decision."""

    route: str
    scenario_id: str
    baseline_experiment_id: int
    candidate_experiment_id: int
    master_seed: int
    replication_count: int
    company_model_version: str
    scenario_schema_version: str
    engine_version: str
    shock_tape_version: str
    baseline_assumptions_hash: str
    candidate_assumptions_hash: str
    comparison_digest: str
    brief_digest: str


def build_flagship_scenario(*, horizon_days: int = 515) -> Scenario:
    """Return the cross-functional policy used in the five-minute demo."""

    if not 1 <= horizon_days <= 3650:
        raise ValueError("horizon_days must be between 1 and 3650")
    baseline = build_baseline_scenario(horizon_days=horizon_days)
    return Scenario(
        scenario_id="service-resilience-plan",
        name="Service resilience plan",
        company_model_version=baseline.company_model_version,
        schema_version=baseline.schema_version,
        horizon_days=baseline.horizon_days,
        warmup_days=baseline.warmup_days,
        evaluation_days=baseline.evaluation_days,
        runoff_days=baseline.runoff_days,
        baseline_scenario_id=baseline.scenario_id,
        policy_levers=PolicyLevers(
            price_changes=(
                SegmentProductPriceChange(
                    segment_id="spot",
                    product_id="intelligent-valve",
                    price_change=Decimal("0.04"),
                ),
            ),
            commercial_investment_change=Decimal("0.02"),
            resource_changes=(
                ResourcePolicyChange(
                    resource_id="assembly",
                    regular_capacity_change=Decimal("0.05"),
                    overtime_capacity_minutes=240,
                ),
                ResourcePolicyChange(
                    resource_id="test",
                    regular_capacity_change=Decimal("0.08"),
                    overtime_capacity_minutes=120,
                ),
            ),
            material_changes=(
                MaterialPolicyChange(
                    material_id="electronics",
                    safety_stock_coverage_days=Decimal("8"),
                    supplier_lead_time_improvement=Decimal("0.15"),
                    supplier_unit_cost_change=Decimal("0.03"),
                ),
            ),
            payment_term_changes=(
                SegmentPaymentTermChange(
                    segment_id="contracted",
                    change_days=-5,
                ),
            ),
            one_off_capital_investment_cents=7_500_000,
        ),
    )


def seed_northstar(session_factory: sessionmaker[Session]) -> bool:
    """Persist the baseline scenario once; return whether it was created."""

    baseline = build_baseline_scenario()
    expected_payload = baseline.model_dump(mode="json")
    with session_factory() as session, session.begin():
        repository = ScenarioRepository(session)
        existing = repository.get(baseline.scenario_id)
        if existing is None:
            repository.create(baseline)
            return True
        if existing.payload != expected_payload:
            raise DemoError(
                "the persisted 'current-plan' scenario differs from the current "
                "Northstar model version"
            )
    return False


def seed_from_settings(settings: Settings | None = None) -> bool:
    """Seed Northstar using the configured relational store."""

    resolved_settings = settings or Settings()
    engine = create_database_engine(resolved_settings)
    try:
        if make_url(resolved_settings.database_url).get_backend_name() == "sqlite":
            Base.metadata.create_all(engine)
        return seed_northstar(create_session_factory(engine))
    finally:
        engine.dispose()


def run_demo(
    client: httpx.Client,
    *,
    frontend_url: str,
    master_seed: int,
    replications: int,
    horizon_days: int = 515,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.5,
) -> DemoResult:
    """Create and run paired experiments through the public API contract."""

    if master_seed < 0:
        raise ValueError("master_seed must be non-negative")
    if not 1 <= replications <= 10_000:
        raise ValueError("replications must be between 1 and 10000")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds cannot be negative")

    baseline = build_baseline_scenario(horizon_days=horizon_days)
    candidate = build_flagship_scenario(horizon_days=horizon_days)
    _ensure_scenario(client, baseline)
    _ensure_scenario(client, candidate)

    request = ExperimentCreate(
        replications=replications,
        master_seed=master_seed,
        max_workers=1,
    )
    baseline_run = _submit_and_wait(
        client,
        baseline,
        request,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    candidate_run = _submit_and_wait(
        client,
        candidate,
        request,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    comparison = _get_model(
        client,
        f"/api/v1/experiments/{candidate_run.id}/comparison",
        ScenarioComparison,
    )
    brief = _get_model(
        client,
        f"/api/v1/experiments/{candidate_run.id}/report",
        ExecutiveBrief,
    )
    provenance = brief.provenance
    route = (
        f"{frontend_url.rstrip('/')}/scenarios/{candidate.scenario_id}/compare"
        f"?experiment={candidate_run.id}"
    )
    return DemoResult(
        route=route,
        scenario_id=candidate.scenario_id,
        baseline_experiment_id=baseline_run.id,
        candidate_experiment_id=candidate_run.id,
        master_seed=provenance.master_seed,
        replication_count=provenance.replication_count,
        company_model_version=provenance.company_model_version,
        scenario_schema_version=provenance.scenario_schema_version,
        engine_version=provenance.engine_version,
        shock_tape_version=provenance.shock_tape_version,
        baseline_assumptions_hash=(provenance.baseline_resolved_assumptions_hash),
        candidate_assumptions_hash=(provenance.candidate_resolved_assumptions_hash),
        comparison_digest=comparison.digest,
        brief_digest=brief.digest,
    )


def format_demo_result(result: DemoResult) -> str:
    """Render stable, copyable output for the local demo command."""

    return "\n".join(
        (
            f"Decision Room: {result.route}",
            f"Scenario: {result.scenario_id}",
            f"Baseline experiment: {result.baseline_experiment_id}",
            f"Candidate experiment: {result.candidate_experiment_id}",
            f"Master seed: {result.master_seed}",
            f"Replications: {result.replication_count}",
            f"Company model: {result.company_model_version}",
            f"Scenario schema: {result.scenario_schema_version}",
            f"Engine: {result.engine_version}",
            f"Shock tape: {result.shock_tape_version}",
            f"Baseline assumptions: {result.baseline_assumptions_hash}",
            f"Candidate assumptions: {result.candidate_assumptions_hash}",
            f"Comparison digest: {result.comparison_digest}",
            f"Brief digest: {result.brief_digest}",
        )
    )


def _ensure_scenario(client: httpx.Client, scenario: Scenario) -> None:
    location = f"/api/v1/scenarios/{scenario.scenario_id}"
    response = client.get(location)
    if response.status_code == 404:
        response = client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        _raise_for_status(response)
        return
    _raise_for_status(response)
    resource = ScenarioRead.model_validate(response.json())
    if resource.id != scenario.scenario_id:
        raise DemoError(
            f"scenario resource '{resource.id}' does not match its canonical ID"
        )
    persisted = Scenario.model_validate(
        resource.model_dump(mode="python", exclude={"id"})
    )
    if persisted != scenario:
        raise DemoError(
            f"scenario '{scenario.scenario_id}' exists with different assumptions"
        )


def _submit_and_wait(
    client: httpx.Client,
    scenario: Scenario,
    request: ExperimentCreate,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> ExperimentRead:
    idempotency_key = (
        f"demo-{scenario.scenario_id}-{scenario.schema_version}-"
        f"{request.master_seed}-{request.replications}"
    )
    response = client.post(
        f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
        json=request.model_dump(mode="json"),
        headers={"Idempotency-Key": idempotency_key},
    )
    _raise_for_status(response)
    experiment = ExperimentRead.model_validate(response.json())
    deadline = monotonic() + timeout_seconds
    while experiment.status not in {"completed", "failed"}:
        if monotonic() >= deadline:
            raise DemoError(
                f"experiment '{experiment.id}' did not complete within "
                f"{timeout_seconds:g} seconds"
            )
        if poll_interval_seconds:
            sleep(poll_interval_seconds)
        response = client.get(f"/api/v1/experiments/{experiment.id}")
        _raise_for_status(response)
        experiment = ExperimentRead.model_validate(response.json())
    if experiment.status == "failed":
        raise DemoError(
            f"experiment '{experiment.id}' failed with "
            f"{experiment.error_code}: {experiment.error_detail}"
        )
    return experiment


def _get_model[ModelT: BaseModel](
    client: httpx.Client,
    path: str,
    model_type: type[ModelT],
) -> ModelT:
    response = client.get(path)
    _raise_for_status(response)
    return model_type.model_validate(response.json())


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        code = payload.get("code", f"http_{response.status_code}")
        detail = payload.get("detail", response.text)
    else:
        code = f"http_{response.status_code}"
        detail = response.text
    raise DemoError(f"API request failed ({code}): {detail}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m openenterprise_twin.cli.demo",
        description="Seed Northstar or create the flagship paired experiment.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="persist the Northstar baseline scenario")
    run = commands.add_parser("run", help="run the flagship paired experiment")
    run.add_argument("--api-url", default=DEFAULT_API_URL)
    run.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    run.add_argument("--seed", type=int, default=DEFAULT_MASTER_SEED)
    run.add_argument("--replications", type=int, default=DEFAULT_REPLICATIONS)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    run.add_argument("--poll-interval", type=float, default=0.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a CLI subcommand and return a shell-compatible status code."""

    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "seed":
            created = seed_from_settings()
            state = "created" if created else "already current"
            print(f"Northstar baseline: {state}")
            return 0
        with httpx.Client(
            base_url=str(arguments.api_url).rstrip("/"),
            timeout=float(arguments.timeout),
        ) as client:
            result = run_demo(
                client,
                frontend_url=str(arguments.frontend_url),
                master_seed=int(arguments.seed),
                replications=int(arguments.replications),
                timeout_seconds=float(arguments.timeout),
                poll_interval_seconds=float(arguments.poll_interval),
            )
        print(format_demo_result(result))
        return 0
    except (DemoError, httpx.HTTPError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
