"""Domain-specific validation errors."""


class DomainValidationError(ValueError):
    """Raised when otherwise valid values violate a domain relationship."""
