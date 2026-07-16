"""In-memory compatibility registry for typed plugin capabilities."""

import inspect
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol, cast, get_type_hints, runtime_checkable

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from openenterprise_twin.plugins.manifest import (
    CapabilityKind,
    PluginManifest,
    parse_semver,
    supports_engine_version,
)
from openenterprise_twin.plugins.protocols import (
    DemandModel,
    DemandModelInput,
    DemandModelOutput,
    FinanceModel,
    FinanceModelInput,
    FinanceModelOutput,
    OperationsModel,
    OperationsModelInput,
    OperationsModelOutput,
    OptimizationStrategy,
    OptimizationStrategyInput,
    OptimizationStrategyOutput,
    ReportSection,
    ReportSectionInput,
    ReportSectionOutput,
    RiskMetric,
    RiskMetricInput,
    RiskMetricOutput,
)

PluginCapability = (
    DemandModel
    | OperationsModel
    | FinanceModel
    | RiskMetric
    | OptimizationStrategy
    | ReportSection
)
PluginInput = (
    DemandModelInput
    | OperationsModelInput
    | FinanceModelInput
    | RiskMetricInput
    | OptimizationStrategyInput
    | ReportSectionInput
)
PluginOutput = (
    DemandModelOutput
    | OperationsModelOutput
    | FinanceModelOutput
    | RiskMetricOutput
    | OptimizationStrategyOutput
    | ReportSectionOutput
)


class PluginRegistryError(ValueError):
    """Base error for invalid registry operations."""


class DuplicateCapabilityError(PluginRegistryError):
    """Raised when a capability identifier is already registered."""


class IncompatiblePluginError(PluginRegistryError):
    """Raised when a plugin does not support the active engine version."""


class InvalidPluginManifestError(PluginRegistryError):
    """Raised when manifest invariants were bypassed before registration."""


class CapabilityContractError(PluginRegistryError):
    """Raised when a capability contradicts its manifest declaration."""


class PluginManifestConflictError(PluginRegistryError):
    """Raised when one plugin ID is associated with different manifests."""


class CapabilityNotFoundError(LookupError):
    """Raised when resolving an unknown capability identifier."""


@runtime_checkable
class _IdentifiedCapability(Protocol):
    @property
    def capability_id(self) -> str: ...


class PluginRegistry:
    """Register compatible implementations and resolve safe typed adapters."""

    def __init__(self, *, engine_version: str) -> None:
        parse_semver(engine_version)
        self._engine_version = engine_version
        self._capabilities: dict[str, PluginCapability] = {}
        self._manifests: dict[str, PluginManifest] = {}

    @property
    def engine_version(self) -> str:
        return self._engine_version

    @property
    def registered_capability_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))

    @property
    def manifests(self) -> Mapping[str, PluginManifest]:
        return MappingProxyType(self._manifests)

    def register(
        self,
        manifest: PluginManifest,
        capability: PluginCapability,
    ) -> None:
        """Validate and register one capability declared by ``manifest``."""

        canonical_manifest = _revalidate_manifest(manifest)
        if not supports_engine_version(canonical_manifest, self._engine_version):
            raise IncompatiblePluginError(
                f"plugin '{canonical_manifest.plugin_id}' does not support engine "
                f"version {self._engine_version}"
            )
        if not isinstance(capability, _IdentifiedCapability):
            raise CapabilityContractError(
                "capability must expose a string capability_id"
            )
        capability_id = capability.capability_id
        if not isinstance(capability_id, str):
            raise CapabilityContractError(
                "capability must expose a string capability_id"
            )

        existing_manifest = self._manifests.get(canonical_manifest.plugin_id)
        if (
            existing_manifest is not None
            and existing_manifest != canonical_manifest
        ):
            raise PluginManifestConflictError(
                f"plugin '{canonical_manifest.plugin_id}' has conflicting manifests"
            )
        if capability_id in self._capabilities:
            raise DuplicateCapabilityError(
                f"capability '{capability_id}' is already registered"
            )

        declaration = next(
            (
                item
                for item in canonical_manifest.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if declaration is None:
            raise CapabilityContractError(
                f"capability '{capability_id}' is not declared by plugin "
                f"'{canonical_manifest.plugin_id}'"
            )
        _validate_implementation(declaration.kind, capability)
        adapter = _build_adapter(declaration.kind, capability)

        self._manifests[canonical_manifest.plugin_id] = canonical_manifest
        self._capabilities[capability_id] = adapter

    def resolve(self, capability_id: str) -> PluginCapability:
        """Resolve a validated adapter or raise a stable lookup error."""

        try:
            return self._capabilities[capability_id]
        except KeyError:
            raise CapabilityNotFoundError(
                f"capability '{capability_id}' is not registered"
            ) from None


def _revalidate_manifest(manifest: PluginManifest) -> PluginManifest:
    try:
        return PluginManifest.model_validate(manifest.model_dump(mode="python"))
    except ValidationError as error:
        raise InvalidPluginManifestError(
            "plugin manifest failed canonical validation"
        ) from error


def _validate_implementation(
    kind: CapabilityKind,
    capability: PluginCapability,
) -> None:
    method_name, input_type, output_type = _method_contract(kind)
    method_object = getattr(capability, method_name, None)
    if not callable(method_object):
        raise CapabilityContractError(
            f"capability '{capability.capability_id}' must expose callable "
            f"'{method_name}' for kind '{kind}'"
        )
    method = cast(Callable[..., object], method_object)
    if (
        inspect.iscoroutinefunction(method)
        or inspect.isgeneratorfunction(method)
        or inspect.isasyncgenfunction(method)
    ):
        raise CapabilityContractError(
            f"capability '{capability.capability_id}' method '{method_name}' "
            "must synchronously return one DTO"
        )
    parameters = tuple(inspect.signature(method).parameters.values())
    if (
        len(parameters) != 1
        or parameters[0].kind is not inspect.Parameter.POSITIONAL_ONLY
        or parameters[0].default is not inspect.Parameter.empty
    ):
        raise CapabilityContractError(
            f"capability '{capability.capability_id}' method '{method_name}' "
            "must accept exactly one required typed input"
        )
    try:
        hints = get_type_hints(method)
    except (NameError, TypeError) as error:
        raise CapabilityContractError(
            f"capability '{capability.capability_id}' has unresolved type hints"
        ) from error
    if (
        hints.get(parameters[0].name) is not input_type
        or hints.get("return") is not output_type
    ):
        raise CapabilityContractError(
            f"capability '{capability.capability_id}' method '{method_name}' "
            f"must use {input_type.__name__} and return {output_type.__name__}"
        )


def _method_contract(
    kind: CapabilityKind,
) -> tuple[str, type[BaseModel], type[BaseModel]]:
    if kind == "demand_model":
        return "forecast", DemandModelInput, DemandModelOutput
    if kind == "operations_model":
        return "plan", OperationsModelInput, OperationsModelOutput
    if kind == "finance_model":
        return "project", FinanceModelInput, FinanceModelOutput
    if kind == "risk_metric":
        return "calculate", RiskMetricInput, RiskMetricOutput
    if kind == "optimization_strategy":
        return "optimize", OptimizationStrategyInput, OptimizationStrategyOutput
    return "render", ReportSectionInput, ReportSectionOutput


def _build_adapter(
    kind: CapabilityKind,
    capability: PluginCapability,
) -> PluginCapability:
    if kind == "demand_model":
        return _DemandModelAdapter(cast(DemandModel, capability))
    if kind == "operations_model":
        return _OperationsModelAdapter(cast(OperationsModel, capability))
    if kind == "finance_model":
        return _FinanceModelAdapter(cast(FinanceModel, capability))
    if kind == "risk_metric":
        return _RiskMetricAdapter(cast(RiskMetric, capability))
    if kind == "optimization_strategy":
        return _OptimizationStrategyAdapter(
            cast(OptimizationStrategy, capability)
        )
    return _ReportSectionAdapter(cast(ReportSection, capability))


def _require_model[ModelT: BaseModel](
    value: object,
    expected_type: type[ModelT],
    *,
    capability_id: str,
) -> ModelT:
    if not isinstance(value, expected_type):
        raise CapabilityContractError(
            f"capability '{capability_id}' returned an invalid "
            f"{expected_type.__name__} result"
        )
    try:
        return expected_type.model_validate(
            value.model_dump(mode="python", warnings="error")
        )
    except (PydanticSerializationError, ValidationError) as error:
        raise CapabilityContractError(
            f"capability '{capability_id}' returned an invalid "
            f"{expected_type.__name__} result"
        ) from error


class _DemandModelAdapter:
    def __init__(self, implementation: DemandModel) -> None:
        self._implementation = implementation

    @property
    def capability_id(self) -> str:
        return self._implementation.capability_id

    def forecast(self, inputs: DemandModelInput, /) -> DemandModelOutput:
        canonical_inputs = _require_model(
            inputs, DemandModelInput, capability_id=self.capability_id
        )
        return _require_model(
            self._implementation.forecast(canonical_inputs),
            DemandModelOutput,
            capability_id=self.capability_id,
        )


class _OperationsModelAdapter:
    def __init__(self, implementation: OperationsModel) -> None:
        self._implementation = implementation

    @property
    def capability_id(self) -> str:
        return self._implementation.capability_id

    def plan(self, inputs: OperationsModelInput, /) -> OperationsModelOutput:
        canonical_inputs = _require_model(
            inputs, OperationsModelInput, capability_id=self.capability_id
        )
        return _require_model(
            self._implementation.plan(canonical_inputs),
            OperationsModelOutput,
            capability_id=self.capability_id,
        )


class _FinanceModelAdapter:
    def __init__(self, implementation: FinanceModel) -> None:
        self._implementation = implementation

    @property
    def capability_id(self) -> str:
        return self._implementation.capability_id

    def project(self, inputs: FinanceModelInput, /) -> FinanceModelOutput:
        canonical_inputs = _require_model(
            inputs, FinanceModelInput, capability_id=self.capability_id
        )
        return _require_model(
            self._implementation.project(canonical_inputs),
            FinanceModelOutput,
            capability_id=self.capability_id,
        )


class _RiskMetricAdapter:
    def __init__(self, implementation: RiskMetric) -> None:
        self._implementation = implementation

    @property
    def capability_id(self) -> str:
        return self._implementation.capability_id

    def calculate(self, inputs: RiskMetricInput, /) -> RiskMetricOutput:
        canonical_inputs = _require_model(
            inputs, RiskMetricInput, capability_id=self.capability_id
        )
        return _require_model(
            self._implementation.calculate(canonical_inputs),
            RiskMetricOutput,
            capability_id=self.capability_id,
        )


class _OptimizationStrategyAdapter:
    def __init__(self, implementation: OptimizationStrategy) -> None:
        self._implementation = implementation

    @property
    def capability_id(self) -> str:
        return self._implementation.capability_id

    def optimize(
        self,
        inputs: OptimizationStrategyInput,
        /,
    ) -> OptimizationStrategyOutput:
        canonical_inputs = _require_model(
            inputs,
            OptimizationStrategyInput,
            capability_id=self.capability_id,
        )
        return _require_model(
            self._implementation.optimize(canonical_inputs),
            OptimizationStrategyOutput,
            capability_id=self.capability_id,
        )


class _ReportSectionAdapter:
    def __init__(self, implementation: ReportSection) -> None:
        self._implementation = implementation

    @property
    def capability_id(self) -> str:
        return self._implementation.capability_id

    def render(self, inputs: ReportSectionInput, /) -> ReportSectionOutput:
        canonical_inputs = _require_model(
            inputs, ReportSectionInput, capability_id=self.capability_id
        )
        return _require_model(
            self._implementation.render(canonical_inputs),
            ReportSectionOutput,
            capability_id=self.capability_id,
        )
