from datetime import datetime

import pytest

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.simulation import experiment as experiment_module
from openenterprise_twin.simulation.engine import simulate_trace
from openenterprise_twin.simulation.experiment import (
    ExperimentRequest,
    experiment_content_digest,
    run_experiment,
    validate_experiment_result,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)
from openenterprise_twin.simulation.shocks import build_shock_tape

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
    request = build_request()
    result = run_experiment(request)

    assert set(result.metrics) == REQUIRED_METRICS
    assert result.scenario_name == request.scenario.name
    assert result.baseline_scenario_id == request.scenario.baseline_scenario_id
    assert result.policy_levers == request.scenario.policy_levers
    assert result.decision_metric_rules == request.company.decision_policy.metric_rules
    assert result.horizon_days == request.scenario.horizon_days
    assert result.warmup_days == request.scenario.warmup_days
    assert result.evaluation_days == request.scenario.evaluation_days
    assert result.runoff_days == request.scenario.runoff_days
    assert len(result.company_model_hash) == 64
    assert result.replication_count == 8
    assert result.company_model_version == "0.2.0"
    assert len(result.resolved_assumptions_hash) == 64
    assert len(result.digest) == 64
    assert isinstance(result.created_at, datetime)
    assert result.created_at.tzinfo is not None
    assert result.duration_seconds >= 0
    assert result.plugin_versions
    assert [item.replication_id for item in result.replications] == list(range(8))
    assert all(len(item.trace_digest) == 64 for item in result.replications)
    assert all(len(item.shock_tape_digest) == 64 for item in result.replications)
    for distribution in result.metrics.values():
        assert distribution.p5 <= distribution.p10
        assert distribution.p10 <= distribution.median
        assert distribution.median <= distribution.p90
        assert distribution.p90 <= distribution.p95
        assert 0 <= distribution.breach_probability <= 1


def test_durable_experiment_artifact_retains_every_full_trace() -> None:
    request = ExperimentRequest(
        company=build_northstar_company(),
        scenario=build_baseline_scenario(horizon_days=3),
        master_seed=731,
        replications=2,
        max_workers=1,
    )

    artifact = experiment_module.run_experiment_with_traces(request)

    assert artifact.schema_version == "0.2.0"
    assert artifact.result.replication_count == 2
    assert [trace.replication_id for trace in artifact.traces] == [0, 1]
    assert all(len(trace.periods) == 3 for trace in artifact.traces)
    assert [trace.digest for trace in artifact.traces] == [
        replication.trace_digest
        for replication in artifact.result.replications
    ]


def test_experiment_is_reproducible() -> None:
    request = build_request()

    first = run_experiment(request)
    second = run_experiment(request)

    assert first.replications == second.replications
    assert first.metric_results == second.metric_results
    assert first.resolved_assumptions_hash == second.resolved_assumptions_hash


def test_experiment_digest_detects_tampered_provenance() -> None:
    result = run_experiment(build_request())
    tampered = result.model_copy(update={"master_seed": result.master_seed + 1})

    with pytest.raises(InvariantViolation, match="experiment_digest"):
        validate_experiment_result(tampered)


def test_validation_recomputes_distributions_from_replication_values() -> None:
    result = run_experiment(build_request())
    first = result.replications[0]
    changed_entries = tuple(
        (name, value + 1.0 if name == "revenue" else value)
        for name, value in first.metric_entries
    )
    changed_replication = first.model_copy(
        update={"metric_entries": changed_entries}
    )
    tampered = result.model_copy(
        update={
            "replications": (changed_replication, *result.replications[1:]),
        }
    )
    tampered = tampered.model_copy(
        update={"digest": experiment_content_digest(tampered)}
    )

    with pytest.raises(InvariantViolation, match="experiment_distribution"):
        validate_experiment_result(tampered)


def test_serial_and_bounded_parallel_execution_are_identical() -> None:
    serial = run_experiment(build_request(max_workers=1))
    parallel = run_experiment(build_request(max_workers=2))

    assert serial.replications == parallel.replications
    assert serial.metric_results == parallel.metric_results
    assert serial.resolved_assumptions_hash == parallel.resolved_assumptions_hash


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
    assert result.metrics["closing_cash"].breach_probability == 1.0


def test_free_cash_flow_includes_capital_investment_charged_during_warmup() -> None:
    company = build_northstar_company()
    baseline = build_baseline_scenario()
    scenario = baseline.model_copy(
        update={
            "policy_levers": baseline.policy_levers.model_copy(
                update={"one_off_capital_investment_cents": 10_000_000}
            )
        }
    )
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=0)
    trace = simulate_trace(
        company, scenario, tape, allow_rescue_funding=True
    )
    evaluation = [period for period in trace.periods if period.phase == "evaluation"]
    expected = sum(
        period.evaluation_origin_collections_cents
        - period.evaluation_origin_supplier_payments_cents
        for period in trace.periods
    ) + (
        trace.periods[-1].closing_evaluation_receivables_cents
        - trace.periods[-1].closing_evaluation_payables_cents
    ) + sum(
        - period.conversion_cost_cents
        - period.overtime_cost_cents
        - period.commercial_investment_change_cents
        - period.capacity_commitment_change_cents
        - period.fixed_cost_cents
        - period.interest_paid_cents
        for period in evaluation
    ) - sum(period.capital_investment_cents for period in trace.periods)
    result = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=scenario,
            master_seed=20260716,
            replications=1,
        )
    )

    assert result.replications[0].metric_values["free_cash_flow"] == expected


def test_free_cash_flow_tracks_evaluation_cohorts_through_runoff() -> None:
    company = build_northstar_company()
    company = company.model_copy(
        update={
            "customer_segments": tuple(
                segment.model_copy(update={"payment_terms_days": 0})
                for segment in company.customer_segments
            )
        }
    )
    scenario = build_baseline_scenario(horizon_days=5).model_copy(
        update={"warmup_days": 1, "evaluation_days": 1, "runoff_days": 3}
    )
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=0)
    tape = tape.model_copy(
        update={
            "days": tuple(
                shock.model_copy(
                    update={
                        "collection_delay_uniform_entries": tuple(
                            (segment_id, 0.0)
                            for segment_id, _ in (
                                shock.collection_delay_uniform_entries
                            )
                        )
                    }
                )
                for shock in tape.days
            )
        }
    )

    trace = simulate_trace(company, scenario, tape)
    metric_values = experiment_module._trace_metric_values(trace)

    runoff_collections = sum(
        period.evaluation_origin_collections_cents
        for period in trace.periods
        if period.phase == "runoff"
    )
    assert runoff_collections > 0
    expected = (
        sum(
            period.evaluation_origin_collections_cents
            - period.evaluation_origin_supplier_payments_cents
            for period in trace.periods
        )
        + trace.periods[-1].closing_evaluation_receivables_cents
        - trace.periods[-1].closing_evaluation_payables_cents
        - sum(
            period.conversion_cost_cents
            + period.overtime_cost_cents
            + period.commercial_investment_change_cents
            + period.capacity_commitment_change_cents
            + period.fixed_cost_cents
            + period.interest_paid_cents
            for period in trace.periods
            if period.phase == "evaluation"
        )
        - sum(period.capital_investment_cents for period in trace.periods)
    )
    assert metric_values["free_cash_flow"] == expected
