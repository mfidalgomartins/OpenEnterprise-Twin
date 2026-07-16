"""Validated model factories shared by backend tests."""

from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.simulation.reference import build_northstar_company as _build


def build_northstar_company() -> CompanyModel:
    return _build()
