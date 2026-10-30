"""Domain exceptions for tenancy (SPRINT-03 B10). Mapped to HTTP in ``router.py``."""

from __future__ import annotations


class TenancyError(Exception):
    """Base class; ``error`` is the machine-readable code used in HTTP bodies."""

    error: str = "tenancy_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.error)
        self.message = message or self.error


class TenantNotFoundError(TenancyError):
    error = "tenant_not_found"


class TenantSlugTakenError(TenancyError):
    error = "tenant_slug_taken"


class BrandNotFoundError(TenancyError):
    """Also raised when the actor has *no* access at all (hides the brand's existence)."""

    error = "brand_not_found"


class BrandSlugTakenError(TenancyError):
    error = "brand_slug_taken"


class InsufficientRoleError(TenancyError):
    error = "insufficient_role"


class GranteeIsOwnerError(TenancyError):
    error = "grantee_is_owner"


class GrantAlreadyActiveError(TenancyError):
    error = "grant_already_active"


class GrantNotFoundError(TenancyError):
    error = "grant_not_found"
