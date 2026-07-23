"""Seed Northstar and create the reproducible flagship Decision Room demo."""

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic, sleep
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.api.schemas import (
    ExperimentCreate,
    ExperimentRead,
    ScenarioRead,
)
from openenterprise_twin.domain.ledger import DecisionContent
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


@dataclass(frozen=True, slots=True)
class AutopilotResult:
    """Evidence from one end-to-end governed decision-loop demo run."""

    dataset_digest: str
    quality_score: float
    credibility_score: float
    credibility_band: str
    backtest_wmape: float
    optimization_digest: str
    frontier_size: int
    evaluations: int
    decision_packet_digest: str
    monitoring_level: str
    recalibration_required: bool


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


def run_autopilot_demo(
    client: httpx.Client,
    *,
    seed: int = DEFAULT_MASTER_SEED,
) -> AutopilotResult:
    """Drive the full closed loop through the API and return its evidence."""

    ingest = _post_json(
        client,
        "/api/v1/datasets/synthetic",
        {"dataset_id": "northstar-history", "days": 540},
    )
    calibration = _post_json(
        client,
        "/api/v1/calibrations",
        {
            "calibration_id": "northstar-cal",
            "dataset_id": "northstar-history",
            "backtest_cutoff": "2024-12-31",
        },
    )
    optimization = _post_json(
        client,
        "/api/v1/optimizations",
        {
            "config": {
                "objectives": [
                    {"metric_name": "ebitda", "direction": "maximize"},
                    {"metric_name": "otif", "direction": "maximize"},
                ],
                "levers": [
                    {
                        "lever_id": "commercial",
                        "kind": "commercial_investment",
                        "lower": -0.1,
                        "upper": 0.3,
                    },
                    {
                        "lever_id": "overtime",
                        "kind": "overtime",
                        "target_id": "assembly",
                        "lower": 0,
                        "upper": 400,
                    },
                ],
                "constraints": [
                    {
                        "metric_name": "rescue_funding",
                        "operator": "lte",
                        "bound": 0,
                        "kind": "hard",
                    }
                ],
                "population_size": 12,
                "max_generations": 5,
                "max_evaluations": 120,
                "seed": seed,
            },
            "horizon_days": 120,
            "replications": 6,
            "master_seed": seed,
        },
    )
    content = _demo_decision_content()
    _post_json(
        client,
        "/api/v1/ledger/decisions",
        {"decision_id": "northstar-pricing", "content": content},
    )
    _walk_decision_to_monitoring(client, "northstar-pricing", content)
    monitoring = _post_json(
        client,
        "/api/v1/ledger/decisions/northstar-pricing/outcomes",
        {
            "predictions": [
                {
                    "metric_name": "ebitda",
                    "expected_mean": 24_000_000.0,
                    "lower": 20_000_000.0,
                    "upper": 28_000_000.0,
                    "improvement_direction": "higher",
                },
                {
                    "metric_name": "otif",
                    "expected_mean": 0.96,
                    "lower": 0.95,
                    "upper": 0.99,
                    "improvement_direction": "higher",
                    "is_hard_constraint": True,
                    "constraint_bound": 0.94,
                },
            ],
            "outcomes": [
                {
                    "metric_name": "ebitda",
                    "as_of": "2026-03-01",
                    "realized_value": 21_500_000.0,
                },
                {
                    "metric_name": "otif",
                    "as_of": "2026-03-01",
                    "realized_value": 0.955,
                },
            ],
        },
    )
    packet = _get_json(
        client, "/api/v1/ledger/decisions/northstar-pricing/packet"
    )
    credibility = calibration["credibility"]
    backtests = calibration["backtests"]
    result = optimization["result"]
    return AutopilotResult(
        dataset_digest=str(ingest["dataset"]["data_digest"]),
        quality_score=float(ingest["quality"]["quality_score"]),
        credibility_score=float(credibility["score"]),
        credibility_band=str(credibility["band"]),
        backtest_wmape=(
            float(backtests[0]["overall_weighted_mape"]) if backtests else 0.0
        ),
        optimization_digest=str(optimization["digest"]),
        frontier_size=len(result["frontier"]),
        evaluations=int(optimization["evaluations"]),
        decision_packet_digest=str(packet["packet_digest"]),
        monitoring_level=str(monitoring["recommended_level"]),
        recalibration_required=bool(monitoring["drift"]["recalibration_required"]),
    )


def _walk_decision_to_monitoring(
    client: httpx.Client,
    decision_id: str,
    content: dict[str, object],
) -> None:
    base = f"/api/v1/ledger/decisions/{decision_id}/transitions"
    _post_json(client, base, {
        "expected_version": 1, "target": "evidence_ready", "actor": "cfo"
    })
    _post_json(client, base, {
        "expected_version": 2, "target": "under_review", "actor": "cfo"
    })
    digest = DecisionContent.model_validate(content).content_digest()
    _post_json(client, base, {
        "expected_version": 3,
        "target": "approved",
        "actor": "ceo",
        "approval": {
            "approver": "ceo",
            "decision": "approve",
            "occurred_at": "2026-07-23T12:00:00Z",
            "approved_content_digest": digest,
        },
    })
    _post_json(client, base, {
        "expected_version": 4, "target": "implemented", "actor": "coo"
    })
    _post_json(client, base, {
        "expected_version": 5, "target": "monitoring", "actor": "coo"
    })


def _demo_decision_content() -> dict[str, object]:
    return {
        "title": "Raise contracted pricing 3% with capacity backstop",
        "owner": "cfo",
        "context": "Margin recovery under stable contracted demand.",
        "objectives": ["grow ebitda", "hold otif >= 0.95"],
        "company_model_version": "0.2.0",
        "recommendation": "Adopt the +3% contracted price with an overtime backstop.",
        "chosen_alternative": "price-plus-3-with-capacity",
        "rejected_alternatives": ["status-quo", "price-plus-3-no-capacity"],
        "justification": (
            "The optimizer's frontier and paired experiments show a material "
            "EBITDA gain while holding the OTIF hard constraint."
        ),
        "hard_constraints": ["otif >= 0.94"],
        "risks": ["Contracted churn if competitors hold price."],
        "evidence": {"experiment_ids": [1, 2]},
    }


def format_autopilot_result(result: "AutopilotResult") -> str:
    return "\n".join(
        (
            "OpenEnterprise Twin — Governed Decision Autopilot",
            f"  data quality score      {result.quality_score:.3f}",
            f"  credibility             {result.credibility_score:.1f} "
            f"({result.credibility_band})",
            f"  backtest wMAPE          {result.backtest_wmape:.3f}",
            f"  optimizer evaluations   {result.evaluations}",
            f"  pareto frontier size    {result.frontier_size}",
            f"  optimization digest     {result.optimization_digest[:16]}",
            f"  decision packet digest  {result.decision_packet_digest[:16]}",
            f"  monitoring outcome      {result.monitoring_level}",
            f"  recalibration required  {result.recalibration_required}",
        )
    )


def _post_json(
    client: httpx.Client, path: str, body: dict[str, object]
) -> dict[str, Any]:
    response = client.post(path, json=body)
    _raise_for_status(response)
    payload = response.json()
    if not isinstance(payload, dict):
        raise DemoError(f"unexpected non-object response from {path}")
    return payload


def _get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    _raise_for_status(response)
    payload = response.json()
    if not isinstance(payload, dict):
        raise DemoError(f"unexpected non-object response from {path}")
    return payload


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
    autopilot = commands.add_parser(
        "autopilot", help="run the end-to-end governed decision-loop demo"
    )
    autopilot.add_argument("--api-url", default=DEFAULT_API_URL)
    autopilot.add_argument("--seed", type=int, default=DEFAULT_MASTER_SEED)
    autopilot.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
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
        if arguments.command == "autopilot":
            with httpx.Client(
                base_url=str(arguments.api_url).rstrip("/"),
                timeout=float(arguments.timeout),
            ) as client:
                autopilot_result = run_autopilot_demo(
                    client, seed=int(arguments.seed)
                )
            print(format_autopilot_result(autopilot_result))
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
