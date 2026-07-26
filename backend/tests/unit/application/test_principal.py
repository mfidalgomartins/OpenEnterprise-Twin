"""Authentication principal and role contracts."""

import pytest

from openenterprise_twin.application.identity import Principal


def test_principal_is_immutable_and_exposes_role_policy() -> None:
    principal = Principal(
        subject="user:finance-01",
        tenant_id="northstar",
        roles=frozenset({"viewer", "analyst"}),
        authentication_method="oidc",
    )

    assert principal.subject == "user:finance-01"
    assert principal.tenant_id == "northstar"
    assert principal.roles == frozenset({"viewer", "analyst"})
    assert principal.has_any_role("analyst")
    assert not principal.has_any_role("approver", "admin")

    with pytest.raises(AttributeError):
        principal.tenant_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("subject", ""),
        ("subject", "contains spaces"),
        ("subject", "x" * 129),
        ("tenant_id", "Northstar"),
        ("tenant_id", "../other"),
        ("tenant_id", "x" * 65),
    ),
)
def test_principal_rejects_unsafe_identifiers(field: str, value: str) -> None:
    values = {
        "subject": "user-1",
        "tenant_id": "northstar",
        "roles": frozenset({"viewer"}),
        "authentication_method": "oidc",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        Principal(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "roles",
    (
        frozenset(),
        frozenset({"owner"}),
        frozenset({"viewer", "root"}),
    ),
)
def test_principal_rejects_empty_or_unknown_roles(
    roles: frozenset[str],
) -> None:
    with pytest.raises(ValueError, match="roles"):
        Principal(
            subject="user-1",
            tenant_id="northstar",
            roles=roles,  # type: ignore[arg-type]
            authentication_method="oidc",
        )
