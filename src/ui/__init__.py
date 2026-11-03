"""L1 operator UI (SPRINT-12.1, D-076 removable).

Server-rendered Arabic/RTL screens mounted under ``/app``. The package talks to the
platform **only** through the public HTTP API (in-process ASGI client) and the auth
module's token codec — never through repositories or SQLAlchemy. Deleting ``src/ui``
and the two lines in ``src/main.py`` removes the feature completely (``REMOVABILITY.md``).
"""
