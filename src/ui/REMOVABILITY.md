# `src/ui` — removability (D-076)

The L1 operator UI is an **optional presentation layer**. Removing it must not change
any API behaviour, migration, or test outside `tests/ui/` and `tests/isolation/`.

## What it owns

* `src/ui/router.py` — `/app/*` routes (login, brand pick, today, inbox) — `include_in_schema=False`.
* `src/ui/session.py` — `app_session` cookie (copy of the auth refresh value, `Path=/app`) + CSRF stamp.
* `src/ui/api.py` — in-process `httpx.ASGITransport` client over the public JSON API.
* `src/ui/templates/**`, `src/ui/i18n/ar.json`, `src/ui/static/vendor/**` (+ `LICENSES.md`).

## What it may import (enforced by `tests/isolation/test_ui_removable.py`)

`fastapi`, `starlette`, `jinja2`, `jwt`, `httpx` (ASGI transport only), the standard
library, `src.core.config`, `src.modules.auth.*` (token codec), and `src.ui.*`.
It never imports `sqlalchemy`, any `*.infrastructure`, repositories, or services.

## What imports it

Only `src/main.py` (two statements: `include_router(ui_router)` + `mount("/app/static", …)`).

## Removal procedure

1. `git rm -r src/ui tests/ui tests/isolation/test_ui_removable.py`
2. Delete the `src.ui` import block and the two mount lines in `src/main.py`.
3. Drop `jinja2` / `python-multipart` from `pyproject.toml` if nothing else needs them.
4. `tests/test_auth_guard.py`: remove `test_app_routes_redirect_without_session`.

No migration, no data, no ErrorCategory, no configuration key is left behind.
