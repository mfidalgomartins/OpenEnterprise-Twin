"""Contracts for immutable, engine-compatible plugin manifests."""

import ast
import json
from importlib import import_module
from itertools import pairwise
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from openenterprise_twin.simulation.engine import simulate_trace
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)
from openenterprise_twin.simulation.shocks import build_shock_tape


def _capability(manifest_module: ModuleType) -> object:
    return manifest_module.CapabilityManifest(
        capability_id="acme.demand.forecast",
        kind="demand_model",
    )


def _manifest(manifest_module: ModuleType, **updates: object) -> object:
    values = {
        "plugin_id": "acme.forecasting",
        "version": "1.2.3",
        "engine_version_min": "0.1.0",
        "engine_version_max": "1.0.0",
        "capabilities": (_capability(manifest_module),),
    }
    values.update(updates)
    return manifest_module.PluginManifest(**values)


def test_valid_manifest_exposes_typed_capabilities() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")
    capability = manifest_module.CapabilityManifest(
        capability_id="acme.demand.forecast",
        kind="demand_model",
        configuration_schema=(
            manifest_module.ConfigurationField(
                name="market",
                value_type="string",
                required=True,
            ),
        ),
    )

    manifest = manifest_module.PluginManifest(
        plugin_id="acme.forecasting",
        version="1.2.3-rc.1+build.7",
        engine_version_min="0.1.0",
        engine_version_max="1.0.0",
        capabilities=(capability,),
    )

    assert manifest.plugin_id == "acme.forecasting"
    assert manifest.capabilities == (capability,)
    assert capability.configuration_schema[0].name == "market"


@pytest.mark.parametrize(
    "version",
    (
        "1",
        "1.2",
        "01.2.3",
        "1.2.3-01",
        "1.1٢.3",
        "v1.2.3",
        "1.2.3+",
    ),
)
def test_manifest_rejects_malformed_semantic_versions(version: str) -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    with pytest.raises(ValidationError, match="valid semantic version"):
        _manifest(manifest_module, version=version)


@pytest.mark.parametrize(
    "plugin_id",
    ("", "Acme.plugin", "acme plugin", "acme/plugin", ".acme", "acme."),
)
def test_manifest_rejects_malformed_plugin_ids(plugin_id: str) -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    with pytest.raises(ValidationError, match="plugin_id must be a valid identifier"):
        _manifest(manifest_module, plugin_id=plugin_id)


@pytest.mark.parametrize(
    "capability_id",
    ("", "Acme.demand", "acme demand", "acme/demand", ".demand", "demand."),
)
def test_manifest_rejects_malformed_capability_ids(capability_id: str) -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    with pytest.raises(
        ValidationError,
        match="capability_id must be a valid identifier",
    ):
        manifest_module.CapabilityManifest(
            capability_id=capability_id,
            kind="demand_model",
        )


@pytest.mark.parametrize(
    "kind",
    (
        "demand_model",
        "operations_model",
        "finance_model",
        "risk_metric",
        "optimization_strategy",
        "report_section",
    ),
)
def test_capability_kinds_match_the_six_protocols(kind: str) -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    capability = manifest_module.CapabilityManifest(
        capability_id="acme.capability",
        kind=kind,
    )

    assert capability.kind == kind


def test_manifest_rejects_unknown_capability_kind_with_stable_error() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    with pytest.raises(
        ValidationError,
        match="kind must match a supported plugin protocol",
    ):
        manifest_module.CapabilityManifest(
            capability_id="acme.capability",
            kind="unknown_model",
        )


def test_manifest_rejects_duplicate_capability_ids() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")
    capability = _capability(manifest_module)

    with pytest.raises(ValidationError, match="capability IDs must be unique"):
        _manifest(manifest_module, capabilities=(capability, capability))


def test_manifest_rejects_inverted_engine_bounds() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    with pytest.raises(
        ValidationError,
        match="engine_version_min must not exceed engine_version_max",
    ):
        _manifest(
            manifest_module,
            engine_version_min="2.0.0",
            engine_version_max="1.9.9",
        )


def test_semver_helpers_follow_prerelease_precedence_and_ignore_build() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")
    ordered_versions = (
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    )

    parsed = manifest_module.parse_semver("2.3.4-rc.1+linux.arm64")

    assert (parsed.major, parsed.minor, parsed.patch) == (2, 3, 4)
    assert parsed.prerelease == ("rc", "1")
    assert parsed.build == ("linux", "arm64")
    assert all(
        manifest_module.compare_semver(left, right) < 0
        for left, right in pairwise(ordered_versions)
    )
    assert manifest_module.compare_semver("1.0.0+first", "1.0.0+second") == 0


@pytest.mark.parametrize(
    ("engine_version", "expected"),
    (
        ("0.0.9", False),
        ("0.1.0", True),
        ("0.5.0", True),
        ("1.0.0", True),
        ("1.0.1", False),
    ),
)
def test_engine_compatibility_includes_both_boundaries(
    engine_version: str,
    expected: bool,
) -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")
    manifest = _manifest(manifest_module)

    assert manifest_module.supports_engine_version(manifest, engine_version) is expected
    assert manifest.supports_engine_version(engine_version) is expected


def test_manifest_requires_at_least_one_capability() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    with pytest.raises(ValidationError, match="at least one capability"):
        _manifest(manifest_module, capabilities=())


def test_configuration_schema_is_typed_and_immutable() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")
    field = manifest_module.ConfigurationField(
        name="market",
        value_type="string",
        required=True,
    )
    capability = manifest_module.CapabilityManifest(
        capability_id="acme.demand.forecast",
        kind="demand_model",
        configuration_schema=(field,),
    )

    assert isinstance(capability.configuration_schema, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        field.required = False
    with pytest.raises(ValidationError, match="frozen"):
        capability.configuration_schema = ()


def test_configuration_schema_rejects_duplicate_field_names() -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")
    field = manifest_module.ConfigurationField(
        name="market",
        value_type="string",
    )

    with pytest.raises(
        ValidationError,
        match="configuration field names must be unique",
    ):
        manifest_module.CapabilityManifest(
            capability_id="acme.demand.forecast",
            kind="demand_model",
            configuration_schema=(field, field),
        )


@pytest.mark.parametrize("name", ("", "Market", "market name", "1market"))
def test_configuration_schema_rejects_invalid_field_names(name: str) -> None:
    manifest_module = import_module("openenterprise_twin.plugins.manifest")

    with pytest.raises(ValidationError, match="valid configuration identifier"):
        manifest_module.ConfigurationField(name=name, value_type="string")


def test_protocols_are_runtime_checkable_and_distinguishable() -> None:
    protocols = import_module("openenterprise_twin.plugins.protocols")

    class DemandCapability:
        capability_id = "acme.demand.forecast"

        def forecast(self, inputs: object) -> object:
            return inputs

    class OperationsCapability:
        capability_id = "acme.operations.plan"

        def plan(self, inputs: object) -> object:
            return inputs

    class FinanceCapability:
        capability_id = "acme.finance.project"

        def project(self, inputs: object) -> object:
            return inputs

    class RiskCapability:
        capability_id = "acme.risk.metric"

        def calculate(self, inputs: object) -> object:
            return inputs

    class OptimizationCapability:
        capability_id = "acme.optimization.strategy"

        def optimize(self, inputs: object) -> object:
            return inputs

    class ReportCapability:
        capability_id = "acme.report.section"

        def render(self, inputs: object) -> object:
            return inputs

    cases = (
        (DemandCapability(), protocols.DemandModel),
        (OperationsCapability(), protocols.OperationsModel),
        (FinanceCapability(), protocols.FinanceModel),
        (RiskCapability(), protocols.RiskMetric),
        (OptimizationCapability(), protocols.OptimizationStrategy),
        (ReportCapability(), protocols.ReportSection),
    )

    assert all(isinstance(capability, protocol) for capability, protocol in cases)
    assert not isinstance(object(), protocols.DemandModel)


def test_protocol_outputs_are_typed_and_immutable() -> None:
    protocols = import_module("openenterprise_twin.plugins.protocols")
    quantity = protocols.QuantityEntry(entity_id="standard-valve", value=12)
    output = protocols.DemandModelOutput(demand_units=(quantity,))

    assert output.demand_units == (quantity,)
    with pytest.raises(ValidationError, match="frozen"):
        quantity.value = 13
    with pytest.raises(ValidationError, match="frozen"):
        output.demand_units = ()


def test_risk_metric_receives_canonical_deeply_immutable_trace_evidence() -> None:
    protocols = import_module("openenterprise_twin.plugins.protocols")
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=1)
    tape = build_shock_tape(company, scenario, seed=7, replication_id=0)
    trace = simulate_trace(company, scenario, tape, allow_rescue_funding=True)
    evidence = protocols.TraceEvidence.from_trace(trace)
    inputs = protocols.RiskMetricInput(trace=evidence)
    original_json = inputs.trace.canonical_json

    decoded = json.loads(original_json)
    decoded["periods"][0]["shipments_units"]["standard-valve"] = 999

    assert inputs.trace.canonical_json == original_json
    assert inputs.trace.digest == trace.digest
    with pytest.raises(ValidationError, match="frozen"):
        inputs.trace.canonical_json = json.dumps(decoded)


def test_plugin_contracts_do_not_import_infrastructure_types() -> None:
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "openenterprise_twin.api",
        "openenterprise_twin.infrastructure",
        "openenterprise_twin.persistence",
    }
    for module_name in (
        "openenterprise_twin.plugins.manifest",
        "openenterprise_twin.plugins.protocols",
    ):
        module = import_module(module_name)
        module_path = Path(module.__file__)
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        annotation_names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }

        assert "Any" not in annotation_names
        assert not any(
            imported == root or imported.startswith(f"{root}.")
            for imported in imported_modules
            for root in forbidden_roots
        )


def test_plugin_package_exports_public_contracts() -> None:
    plugins = import_module("openenterprise_twin.plugins")
    expected_names = {
        "CapabilityManifest",
        "ConfigurationField",
        "DemandModel",
        "FinanceModel",
        "OperationsModel",
        "OptimizationStrategy",
        "PluginManifest",
        "ReportSection",
        "RiskMetric",
        "compare_semver",
        "parse_semver",
        "supports_engine_version",
    }

    assert expected_names <= set(plugins.__all__)
    assert all(hasattr(plugins, name) for name in expected_names)
