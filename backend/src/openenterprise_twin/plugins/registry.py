"""In-memory compatibility registry for typed plugin capabilities."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol, cast, runtime_checkable

from openenterprise_twin.plugins.manifest import (
    CapabilityKind,
    PluginManifest,
    parse_semver,
    supports_engine_version,
)
from openenterprise_twin.plugins.protocols import (
    DemandModel,
    FinanceModel,
    OperationsModel,
    OptimizationStrategy,
    ReportSection,
    RiskMetric,
)

PluginCapability = (
    DemandModel
    | OperationsModel
    | FinanceModel
    | RiskMetric
    | OptimizationStrategy
    | ReportSection
)


class PluginRegistryError(ValueError):
    """Base error for invalid registry operations."""


class DuplicateCapabilityError(PluginRegistryError):
    """Raised when a capability identifier is already registered."""


class IncompatiblePluginError(PluginRegistryError):
    """Raised when a plugin does not support the active engine version."""


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
    """Register compatible implementations and resolve them by stable ID."""

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

    def register(self, manifest: PluginManifest, capability: object) -> None:
        """Validate and register one capability declared by ``manifest``."""

        if not supports_engine_version(manifest, self._engine_version):
            raise IncompatiblePluginError(
                f"plugin '{manifest.plugin_id}' does not support engine "
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
        if capability_id in self._capabilities:
            raise DuplicateCapabilityError(
                f"capability '{capability_id}' is already registered"
            )

        declaration = next(
            (
                item
                for item in manifest.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if declaration is None:
            raise CapabilityContractError(
                f"capability '{capability_id}' is not declared by plugin "
                f"'{manifest.plugin_id}'"
            )
        if not _matches_protocol(declaration.kind, capability):
            raise CapabilityContractError(
                f"capability '{capability_id}' does not implement declared "
                f"kind '{declaration.kind}'"
            )

        existing_manifest = self._manifests.get(manifest.plugin_id)
        if existing_manifest is not None and existing_manifest != manifest:
            raise PluginManifestConflictError(
                f"plugin '{manifest.plugin_id}' has conflicting manifests"
            )
        self._manifests[manifest.plugin_id] = manifest
        self._capabilities[capability_id] = cast(PluginCapability, capability)

    def resolve(self, capability_id: str) -> PluginCapability:
        """Resolve a registered capability or raise a stable lookup error."""

        try:
            return self._capabilities[capability_id]
        except KeyError:
            raise CapabilityNotFoundError(
                f"capability '{capability_id}' is not registered"
            ) from None


def _matches_protocol(kind: CapabilityKind, capability: object) -> bool:
    if kind == "demand_model":
        return isinstance(capability, DemandModel)
    if kind == "operations_model":
        return isinstance(capability, OperationsModel)
    if kind == "finance_model":
        return isinstance(capability, FinanceModel)
    if kind == "risk_metric":
        return isinstance(capability, RiskMetric)
    if kind == "optimization_strategy":
        return isinstance(capability, OptimizationStrategy)
    return isinstance(capability, ReportSection)
