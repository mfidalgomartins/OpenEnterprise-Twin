"""Immutable declarations for plugin capabilities and compatibility."""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationInfo,
    field_validator,
    model_validator,
)

CapabilityKind = Literal[
    "demand_model",
    "operations_model",
    "finance_model",
    "risk_metric",
    "optimization_strategy",
    "report_section",
]
CAPABILITY_KINDS: tuple[CapabilityKind, ...] = (
    "demand_model",
    "operations_model",
    "finance_model",
    "risk_metric",
    "optimization_strategy",
    "report_section",
)
ConfigurationValueType = Literal["string", "integer", "number", "boolean"]

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_CONFIGURATION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True, slots=True)
class SemanticVersion:
    """Parsed SemVer 2.0.0 components."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()


def parse_semver(version: str) -> SemanticVersion:
    """Parse a SemVer 2.0.0 string or raise a stable ``ValueError``."""

    match = _SEMVER_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"'{version}' is not a valid semantic version")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    build = tuple(match.group(5).split(".")) if match.group(5) else ()
    return SemanticVersion(
        major=int(match.group(1)),
        minor=int(match.group(2)),
        patch=int(match.group(3)),
        prerelease=prerelease,
        build=build,
    )


def compare_semver(left: str, right: str) -> int:
    """Return -1, 0 or 1 using SemVer precedence rules."""

    left_version = parse_semver(left)
    right_version = parse_semver(right)
    left_core = (left_version.major, left_version.minor, left_version.patch)
    right_core = (right_version.major, right_version.minor, right_version.patch)
    if left_core != right_core:
        return -1 if left_core < right_core else 1
    return _compare_prerelease(left_version.prerelease, right_version.prerelease)


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right, strict=False):
        if left_part == right_part:
            continue
        left_is_numeric = left_part.isdigit()
        right_is_numeric = right_part.isdigit()
        if left_is_numeric and right_is_numeric:
            return -1 if int(left_part) < int(right_part) else 1
        if left_is_numeric != right_is_numeric:
            return -1 if left_is_numeric else 1
        return -1 if left_part < right_part else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def _is_valid_identifier(value: str) -> bool:
    return len(value) <= 128 and _IDENTIFIER_PATTERN.fullmatch(value) is not None


class ManifestModel(BaseModel):
    """Strict immutable base for plugin metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ConfigurationField(ManifestModel):
    """One typed scalar setting accepted by a capability."""

    name: str
    value_type: ConfigurationValueType
    required: bool = False
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if (
            len(value) > 128
            or _CONFIGURATION_NAME_PATTERN.fullmatch(value) is None
        ):
            raise ValueError("name must be a valid configuration identifier")
        return value


class CapabilityManifest(ManifestModel):
    """One capability exported by a plugin."""

    capability_id: str
    kind: CapabilityKind
    configuration_schema: tuple[ConfigurationField, ...] = ()

    @field_validator("kind", mode="before")
    @classmethod
    def validate_kind(cls, value: object) -> object:
        if not isinstance(value, str) or value not in CAPABILITY_KINDS:
            raise ValueError("kind must match a supported plugin protocol")
        return value

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        if not _is_valid_identifier(value):
            raise ValueError("capability_id must be a valid identifier")
        return value

    @model_validator(mode="after")
    def validate_configuration_schema(self) -> "CapabilityManifest":
        names = tuple(field.name for field in self.configuration_schema)
        if len(names) != len(set(names)):
            raise ValueError("configuration field names must be unique")
        return self


class PluginManifest(ManifestModel):
    """Versioned plugin metadata consumed during registration."""

    plugin_id: str
    version: str
    engine_version_min: str
    engine_version_max: str
    capabilities: tuple[CapabilityManifest, ...]

    @field_validator("plugin_id")
    @classmethod
    def validate_plugin_id(cls, value: str) -> str:
        if not _is_valid_identifier(value):
            raise ValueError("plugin_id must be a valid identifier")
        return value

    @field_validator("version", "engine_version_min", "engine_version_max")
    @classmethod
    def validate_semantic_version(cls, value: str, info: ValidationInfo) -> str:
        try:
            parse_semver(value)
        except ValueError:
            raise ValueError(
                f"{info.field_name} must be a valid semantic version"
            ) from None
        return value

    @model_validator(mode="after")
    def validate_capabilities_and_bounds(self) -> "PluginManifest":
        if not self.capabilities:
            raise ValueError("plugin manifest must declare at least one capability")
        capability_ids = tuple(
            capability.capability_id for capability in self.capabilities
        )
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability IDs must be unique")
        if compare_semver(self.engine_version_min, self.engine_version_max) > 0:
            raise ValueError("engine_version_min must not exceed engine_version_max")
        return self

    def supports_engine_version(self, engine_version: str) -> bool:
        """Return whether an engine version is inside the inclusive range."""

        return supports_engine_version(self, engine_version)


def supports_engine_version(
    manifest: PluginManifest,
    engine_version: str,
) -> bool:
    """Return whether ``manifest`` supports an engine version inclusively."""

    return (
        compare_semver(manifest.engine_version_min, engine_version) <= 0
        and compare_semver(engine_version, manifest.engine_version_max) <= 0
    )
