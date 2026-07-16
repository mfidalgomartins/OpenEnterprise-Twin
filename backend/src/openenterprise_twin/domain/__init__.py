"""Immutable domain models for OpenEnterprise Twin."""

from openenterprise_twin.domain.company import (
    CompanyModel,
    CustomerSegment,
    DemandProfile,
    FinancialPolicy,
    MaterialPolicy,
    MaterialRequirement,
    Plant,
    Product,
    ResourceCapacity,
    ResourceRequirement,
)
from openenterprise_twin.domain.scenario import (
    MaterialPolicyChange,
    PolicyLevers,
    ResourcePolicyChange,
    Scenario,
    SegmentPaymentTermChange,
    SegmentProductPriceChange,
    validate_scenario_against_company,
)

__all__ = [
    "CompanyModel",
    "CustomerSegment",
    "DemandProfile",
    "FinancialPolicy",
    "MaterialPolicy",
    "MaterialPolicyChange",
    "MaterialRequirement",
    "Plant",
    "PolicyLevers",
    "Product",
    "ResourceCapacity",
    "ResourcePolicyChange",
    "ResourceRequirement",
    "Scenario",
    "SegmentPaymentTermChange",
    "SegmentProductPriceChange",
    "validate_scenario_against_company",
]
