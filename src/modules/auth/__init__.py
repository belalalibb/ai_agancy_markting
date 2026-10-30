"""Auth module (SPRINT-04): users, sessions, access JWTs and the verified actor identity.

Import direction (ARCHITECTURE_LOCK D-013): ``tenancy.dependencies`` may import
``auth.dependencies``; ``auth`` imports only ``tenancy.domain`` / ``tenancy.service`` /
``tenancy.router`` (for ``get_tenancy_service`` + the error mapper).
"""

from src.modules.auth.router import router

__all__ = ["router"]
