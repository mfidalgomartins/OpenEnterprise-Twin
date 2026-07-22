import math
from typing import Literal

import pytest
from pydantic import ValidationError

from openenterprise_twin.simulation.metrics import (
    MetricDistribution,
    summarize_distribution,
)


def test_summarize_distribution_uses_linear_quantiles_and_population_std() -> None:
    result = summarize_distribution(
        [0.0, 10.0, 20.0, 30.0, 40.0],
        guardrail=20.0,
        breach_when="below",
        downside_tail="lower",
    )

    assert result.mean == pytest.approx(20.0)
    assert result.median == pytest.approx(20.0)
    assert result.p5 == pytest.approx(2.0)
    assert result.p10 == pytest.approx(4.0)
    assert result.p90 == pytest.approx(36.0)
    assert result.p95 == pytest.approx(38.0)
    assert result.standard_deviation == pytest.approx(math.sqrt(200.0))


@pytest.mark.parametrize("breach_when", ["below", "above"])
def test_breach_probability_is_strict_and_excludes_guardrail_equality(
    breach_when: Literal["below", "above"],
) -> None:
    result = summarize_distribution(
        [10.0, 20.0, 30.0],
        guardrail=20.0,
        breach_when=breach_when,
        downside_tail="lower",
    )

    assert result.breach_probability == pytest.approx(1 / 3)
    assert result.breach_probability_ci95_lower == pytest.approx(0.0614919447)
    assert result.breach_probability_ci95_upper == pytest.approx(0.7923403992)


@pytest.mark.parametrize(
    ("downside_tail", "expected_cvar95"),
    [("lower", 1 / 21), ("upper", 20 - 1 / 21)],
)
def test_cvar95_fractionally_weights_the_empirical_five_percent_tail(
    downside_tail: Literal["lower", "upper"], expected_cvar95: float
) -> None:
    result = summarize_distribution(
        range(21),
        guardrail=10.0,
        breach_when="below",
        downside_tail=downside_tail,
    )

    assert result.cvar95 == pytest.approx(expected_cvar95)


def test_singleton_distribution_has_identical_quantiles_and_zero_dispersion() -> None:
    result = summarize_distribution(
        [7.5],
        guardrail=7.5,
        breach_when="below",
        downside_tail="lower",
    )

    assert result == MetricDistribution(
        mean=7.5,
        median=7.5,
        p5=7.5,
        p10=7.5,
        p90=7.5,
        p95=7.5,
        standard_deviation=0.0,
        breach_probability=0.0,
        breach_probability_ci95_lower=0.0,
        breach_probability_ci95_upper=0.7934506856227626,
        cvar95=7.5,
    )


def test_summarization_is_exactly_deterministic() -> None:
    values = (3.25, -1.5, 8.0, 8.0, 2.75)

    first = summarize_distribution(values, 0.0, "below", "lower")
    second = summarize_distribution(values, 0.0, "below", "lower")

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.parametrize(
    "values",
    [(), (math.nan,), (math.inf,), (-math.inf,), (1.0, math.nan)],
)
def test_summarize_distribution_rejects_empty_or_non_finite_values(
    values: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError, match="values"):
        summarize_distribution(
            values,
            guardrail=0.0,
            breach_when="below",
            downside_tail="lower",
        )


@pytest.mark.parametrize("guardrail", [math.nan, math.inf, -math.inf, True])
def test_summarize_distribution_rejects_non_finite_guardrail(
    guardrail: float,
) -> None:
    with pytest.raises(ValueError, match="guardrail"):
        summarize_distribution(
            [1.0, 2.0],
            guardrail=guardrail,
            breach_when="below",
            downside_tail="lower",
        )


def test_summarize_distribution_rejects_invalid_literal_modes() -> None:
    with pytest.raises(ValueError, match="breach_when"):
        summarize_distribution([1.0], 0.0, "invalid", "lower")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="downside_tail"):
        summarize_distribution([1.0], 0.0, "below", "invalid")  # type: ignore[arg-type]


def test_metric_distribution_is_strict_and_immutable() -> None:
    result = summarize_distribution(
        [1.0, 2.0],
        guardrail=1.0,
        breach_when="above",
        downside_tail="upper",
    )
    invalid_data = result.model_dump()
    invalid_data["mean"] = "1.5"

    with pytest.raises(ValidationError):
        MetricDistribution.model_validate(invalid_data)
    with pytest.raises(ValidationError, match="frozen"):
        result.mean = 2.0
