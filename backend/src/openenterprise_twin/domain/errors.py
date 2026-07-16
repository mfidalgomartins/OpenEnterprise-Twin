"""Domain-specific validation errors."""


class DomainValidationError(ValueError):
    """Raised when otherwise valid values violate a domain relationship."""


class InvariantViolation(RuntimeError):  # noqa: N818 - established domain term
    """Raised when a simulation transition breaks a physical or financial law."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")
