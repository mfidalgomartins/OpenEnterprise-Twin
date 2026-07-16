"""Behavioral contracts for safe plugin capability registration."""

import pytest

from openenterprise_twin.plugins.manifest import (
    CapabilityManifest,
    PluginManifest,
)
from openenterprise_twin.plugins.protocols import (
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
    PluginManifestConflictError,
    PluginRegistry,
)


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

    assert registry.resolve(capability.capability_id) is capability
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


def test_registry_rejects_unknown_resolution() -> None:
    registry = PluginRegistry(engine_version="0.1.0")

    with pytest.raises(CapabilityNotFoundError, match=r"missing\.capability"):
        registry.resolve("missing.capability")
