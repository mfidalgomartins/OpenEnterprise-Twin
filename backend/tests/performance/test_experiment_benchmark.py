import os

import pytest

from openenterprise_twin.simulation.experiment import (
    ExperimentRequest,
    run_experiment,
    validate_experiment_result,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)


@pytest.mark.performance
def test_one_thousand_full_horizon_replications_are_valid() -> None:
    request = ExperimentRequest(
        company=build_northstar_company(),
        scenario=build_baseline_scenario(),
        master_seed=20260716,
        replications=1_000,
        max_workers=min(24, os.cpu_count() or 1),
    )

    result = run_experiment(request)

    validate_experiment_result(result)
    assert result.replication_count == 1_000
    assert len(result.replications) == 1_000
    assert all(
        len(replication.trace_digest) == 64
        for replication in result.replications
    )
