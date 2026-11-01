"""Domain errors of the entitlements module (HTTP mapping lives in ``router.py``)."""

from __future__ import annotations


class EntitlementsError(Exception):
    error = "entitlements_error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.error.replace("_", " ")
        super().__init__(self.message)


class BrandNotFoundError(EntitlementsError):
    error = "brand_not_found"


class InvalidActivationError(EntitlementsError):
    error = "invalid_activation"
