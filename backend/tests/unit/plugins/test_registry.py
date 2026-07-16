"""Behavioral contracts for safe plugin capability registration."""

from typing import cast

import pytest

from openenterprise_twin.plugins.manifest import (
    CapabilityManifest,
    PluginManifest,
)
from openenterprise_twin.plugins.protocols import (
    DemandModel,
    DemandModelInput,
    DemandModelOutput,
    RiskMetricInput,
    RiskMetricOutput,
)
from openenterprise_twin.plugins.registry import (
    CapabilityContractError,
    CapabilityNotFoundError,
    DuplicateCapabilityError,
    IncompatiblePluginError,
    InvalidPluginManifestError,
    PluginManifestConflictError,
    PluginRegistry,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)
from openenterprise_twin.simulation.shocks import build_shock_tape


class DemandCapability:
    capability_id = "acme.demand.forecast"

    def forecast(self, inputs: DemandModelInput, /) -> DemandModelOutput:
        return DemandModelOutput(demand_units=())


class RiskCapability:
    capability_id = "acme.risk.liquidity"

    def calculate(self, inputs: RiskMetricInput, /) -> RiskMetricOutput:
        return RiskMetricOutput(metric_id=self.capability_id, value=0.0)


class InvalidDemandCapability:
    capability_id = "acme.demand.forecast"

    def calculate(self, inputs: RiskMetricInput, /) -> RiskMetricOutput:
        return RiskMetricOutput(metric_id=self.capability_id, value=0.0)


class WrongArityDemandCapability:
    capability_id = "acme.demand.forecast"

    def forecast(
        self,
        inputs: DemandModelInput,
        extra: DemandModelInput,
        /,
    ) -> DemandModelOutput:
        return DemandModelOutput(demand_units=())


class NonCallableDemandCapability:
    capability_id = "acme.demand.forecast"
    forecast = "not-callable"


class AsyncDemandCapability:
    capability_id = "acme.demand.forecast"

    async def forecast(self, inputs: DemandModelInput, /) -> DemandModelOutput:
        return DemandModelOutput(demand_units=())


class WrongAnnotationDemandCapability:
    capability_id = "acme.demand.forecast"

    def forecast(self, inputs: object, /) -> object:
        return inputs


class LyingDemandCapability:
    capability_id = "acme.demand.forecast"

    def forecast(self, inputs: DemandModelInput, /) -> DemandModelOutput:
        return cast(DemandModelOutput, object())


def _manifest(
    *,
    version: str = "1.2.3",
    engine_version_min: str = "0.1.0",
    engine_version_max: str = "0.9.0",
    capabilities: tuple[CapabilityManifest, ...] | None = None,
) -> PluginManifest:
    return PluginManifest(
        plugin_id="acme.analytics",
        version=version,
        engine_version_min=engine_version_min,
        engine_version_max=engine_version_max,
        capabilities=capabilities
        or (
            CapabilityManifest(
                capability_id="acme.demand.forecast",
                kind="demand_model",
            ),
        ),
    )


def test_registry_registers_and_resolves_a_typed_capability() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    capability = DemandCapability()

    registry.register(_manifest(), capability)

    resolved = registry.resolve(capability.capability_id)
    assert resolved is not capability
    assert resolved.capability_id == capability.capability_id
    assert registry.registered_capability_ids == (capability.capability_id,)


def test_registry_rejects_duplicate_capability() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    capability = DemandCapability()
    registry.register(_manifest(), capability)

    with pytest.raises(DuplicateCapabilityError, match=capability.capability_id):
        registry.register(_manifest(), capability)


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    (("0.2.0", "1.0.0"), ("0.0.1", "0.0.9")),
)
def test_registry_rejects_incompatible_engine_ranges(
    minimum: str,
    maximum: str,
) -> None:
    registry = PluginRegistry(engine_version="0.1.0")

    with pytest.raises(IncompatiblePluginError, match=r"0\.1\.0"):
        registry.register(
            _manifest(
                engine_version_min=minimum,
                engine_version_max=maximum,
            ),
            DemandCapability(),
        )


def test_registry_rejects_capability_missing_from_manifest() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    manifest = _manifest(
        capabilities=(
            CapabilityManifest(
                capability_id="acme.risk.liquidity",
                kind="risk_metric",
            ),
        )
    )

    with pytest.raises(CapabilityContractError, match="not declared"):
        registry.register(manifest, DemandCapability())


def test_registry_rejects_implementation_that_does_not_match_declared_kind() -> None:
    registry = PluginRegistry(engine_version="0.1.0")

    with pytest.raises(CapabilityContractError, match="demand_model"):
        registry.register(_manifest(), InvalidDemandCapability())


@pytest.mark.parametrize(
    "capability",
    (
        WrongArityDemandCapability(),
        NonCallableDemandCapability(),
        AsyncDemandCapability(),
        WrongAnnotationDemandCapability(),
    ),
)
def test_registry_rejects_invalid_runtime_method_contracts(
    capability: object,
) -> None:
    registry = PluginRegistry(engine_version="0.1.0")

    with pytest.raises(CapabilityContractError):
        registry.register(
            _manifest(),
            cast(DemandModel, capability),
        )


def test_resolved_adapter_rejects_a_runtime_output_that_breaks_its_annotation() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    registry.register(_manifest(), LyingDemandCapability())
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=1)
    inputs = DemandModelInput(
        company=company,
        scenario=scenario,
        shock_tape=build_shock_tape(
            company,
            scenario,
            seed=7,
            replication_id=0,
        ),
    )

    with pytest.raises(CapabilityContractError, match="invalid DemandModelOutput"):
        cast(DemandModel, registry.resolve("acme.demand.forecast")).forecast(inputs)


def test_registry_accepts_multiple_capabilities_from_one_manifest() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    manifest = _manifest(
        capabilities=(
            CapabilityManifest(
                capability_id="acme.demand.forecast",
                kind="demand_model",
            ),
            CapabilityManifest(
                capability_id="acme.risk.liquidity",
                kind="risk_metric",
            ),
        )
    )

    registry.register(manifest, RiskCapability())
    registry.register(manifest, DemandCapability())

    assert registry.registered_capability_ids == (
        "acme.demand.forecast",
        "acme.risk.liquidity",
    )


def test_registry_rejects_conflicting_manifests_for_same_plugin() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    manifest = _manifest(
        capabilities=(
            CapabilityManifest(
                capability_id="acme.demand.forecast",
                kind="demand_model",
            ),
            CapabilityManifest(
                capability_id="acme.risk.liquidity",
                kind="risk_metric",
            ),
        )
    )
    registry.register(manifest, DemandCapability())

    with pytest.raises(PluginManifestConflictError, match=r"acme\.analytics"):
        registry.register(
            manifest.model_copy(update={"version": "1.2.4"}),
            RiskCapability(),
        )


def test_manifest_conflict_precedes_duplicate_capability_classification() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    registry.register(_manifest(), DemandCapability())

    with pytest.raises(PluginManifestConflictError):
        registry.register(
            _manifest(version="1.2.4"),
            DemandCapability(),
        )


def test_registry_revalidates_manifest_instances_at_the_trust_boundary() -> None:
    registry = PluginRegistry(engine_version="0.1.0")
    invalid = _manifest().model_copy(update={"version": "not-semver"})

    with pytest.raises(InvalidPluginManifestError):
        registry.register(invalid, DemandCapability())


def test_registry_rejects_unknown_resolution() -> None:
    registry = PluginRegistry(engine_version="0.1.0")

    with pytest.raises(CapabilityNotFoundError, match=r"missing\.capability"):
        registry.resolve("missing.capability")
