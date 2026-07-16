import pytest

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.simulation.experiment import (
    ExperimentRequest,
    run_experiment,
    validate_experiment_result,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)

REQUIRED_METRICS = {
    "revenue",
    "ebitda",
    "free_cash_flow",
    "closing_cash",
    "otif",
    "cancellation_rate",
    "backlog_units",
    "capacity_utilization",
    "peak_revolver",
    "rescue_funding",
}


def build_request(*, max_workers: int = 1) -> ExperimentRequest:
    return ExperimentRequest(
        company=build_northstar_company(),
        scenario=build_baseline_scenario(horizon_days=30),
        master_seed=20260716,
        replications=8,
        max_workers=max_workers,
    )


def test_experiment_exposes_required_distributions_and_replications() -> None:
    result = run_experiment(build_request())

    assert set(result.metrics) == REQUIRED_METRICS
    assert result.replication_count == 8
    assert result.company_model_version == "0.1.0"
    assert len(result.resolved_assumptions_hash) == 64
    assert len(result.digest) == 64
    assert [item.replication_id for item in result.replications] == list(range(8))
    assert all(len(item.trace_digest) == 64 for item in result.replications)
    for distribution in result.metrics.values():
        assert distribution.p5 <= distribution.p10
        assert distribution.p10 <= distribution.median
        assert distribution.median <= distribution.p90
        assert distribution.p90 <= distribution.p95
        assert 0 <= distribution.breach_probability <= 1


def test_experiment_is_reproducible() -> None:
    request = build_request()

    assert run_experiment(request) == run_experiment(request)


def test_experiment_digest_detects_tampered_provenance() -> None:
    result = run_experiment(build_request())
    tampered = result.model_copy(update={"master_seed": result.master_seed + 1})

    with pytest.raises(InvariantViolation, match="experiment_digest"):
        validate_experiment_result(tampered)


def test_serial_and_bounded_parallel_execution_are_identical() -> None:
    serial = run_experiment(build_request(max_workers=1))
    parallel = run_experiment(build_request(max_workers=2))

    assert serial == parallel


def test_rescue_funding_probability_is_observed_for_infeasible_cash_plan() -> None:
    company = build_northstar_company().model_copy(
        update={
            "financial_policy": build_northstar_company().financial_policy.model_copy(
                update={"opening_cash_cents": 5_000_000, "revolver_limit_cents": 0}
            )
        }
    )
    scenario = build_baseline_scenario(horizon_days=5).model_copy(
        update={
            "policy_levers": build_baseline_scenario(
                horizon_days=5
            ).policy_levers.model_copy(
                update={"one_off_capital_investment_cents": 50_000_000}
            )
        }
    )
    request = ExperimentRequest(
        company=company,
        scenario=scenario,
        master_seed=7,
        replications=3,
        max_workers=1,
    )

    result = run_experiment(request)

    assert result.metrics["rescue_funding"].breach_probability == 1.0
    assert result.metrics["rescue_funding"].mean > 0
