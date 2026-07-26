"""Infrastructure-neutral authenticated identity and authorization primitives."""

from dataclasses import dataclass
from re import fullmatch
from typing import Literal, Protocol, runtime_checkable

Role = Literal["viewer", "analyst", "approver", "admin"]
AuthenticationMethod = Literal["local", "api_key", "oidc"]

ROLES: frozenset[str] = frozenset(
    {"viewer", "analyst", "approver", "admin"}
)
_SUBJECT_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:@|/-]{0,127}"
_TENANT_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}"


class AuthenticationError(Exception):
    """Safe authentication failure without credential-bearing detail."""

    def __init__(
        self,
        detail: str = "The supplied credentials could not be authenticated.",
    ) -> None:
        super().__init__(detail)
        self.code = "authentication_required"
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Principal:
    """Immutable authenticated identity used by application policy."""

    subject: str
    tenant_id: str
    roles: frozenset[Role]
    authentication_method: AuthenticationMethod

    def __post_init__(self) -> None:
        if fullmatch(_SUBJECT_PATTERN, self.subject) is None:
            raise ValueError("subject is not a safe bounded identifier")
        if fullmatch(_TENANT_PATTERN, self.tenant_id) is None:
            raise ValueError("tenant_id is not a safe bounded identifier")
        if not self.roles or not set(self.roles).issubset(ROLES):
            raise ValueError("roles must contain only supported roles")

    def has_any_role(self, *roles: Role) -> bool:
        """Return whether this principal has at least one required role."""

        return not self.roles.isdisjoint(roles)


@runtime_checkable
class IdentityProvider(Protocol):
    """Authenticate one transport credential set into a principal."""

    def authenticate(
        self,
        *,
        api_key: str | None,
        bearer_token: str | None,
    ) -> Principal: ...
