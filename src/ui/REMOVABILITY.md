# `src/ui` — removability (D-076)

The L1 operator UI is an **optional presentation layer**. Removing it must not change
any API behaviour, migration, or test outside `tests/ui/` and `tests/isolation/`.

## What it owns

* `src/ui/router.py` — `/app/*` routes (login, **register** (SPRINT-14-A A-1: one
  `POST /auth/register` ⇒ session via `_establish_session`, shared with login), brand pick
  (+ **first-brand form** `POST /app/brands` with `derive_slug` and the **onboarding bar** — A-2/A-5),
  today, inbox, content, diagnosis, settings (+ **WhatsApp connect/rotate** `POST …/settings/whatsapp[/rotate]`
  with `wa_*` form names mapped by `_wa_payload` — A-4), the **Meta OAuth browser bridge**
  `GET /app/social/meta/callback` and the **page-selection screen** `GET/POST …/connections/{cid}/pages`
  (A-3; the JSON callback under `/api/v1` is untouched) — SPRINT-12.1-B C; `/app/admin/ai` 4 tabs + probe + SPRINT-13 `audit` (last 50
  `admin_actions` + platform flags) and `dead_letter` (failed publish jobs + requeue) tabs, and
  `/app/admin/entitlements` with suspend/unsuspend — D, non-admins ⇒ 404 before any API call) —
  `include_in_schema=False`. Every screen reads/writes only through
  `ApiClient`; roles, entitlements, consent and the content state machine are enforced upstream.
* `src/ui/slug.py` — SPRINT-14-A §4-ب: `derive_slug(name, prefix)` / `slug_retry` — pure text
  transformation for Arabic names (no business rule; the API still validates `Slug`).
* `src/ui/session.py` — three `Path=/app` cookies: `app_session` (copy of the auth refresh value),
  `app_access` (the short-lived access JWT, decoded locally — `/auth/refresh` is called only on
  expiry, SPRINT-12.1-B C-0) and `app_csrf` (per-login nonce) + the CSRF stamp
  `HMAC(key, user_id:nonce)[:32]`. An in-process `asyncio.Lock` coalesces twin refreshes.
* `src/ui/api.py` — in-process ASGI client over the public JSON API (transport built by
  `src.core.http.build_in_process_client`; `src/ui` itself never imports `httpx` — D-068).
* `src/ui/templates/**`, `src/ui/i18n/ar.json`, `src/ui/static/vendor/**` (+ `LICENSES.md`).
* The optional Umami tag (`Settings.umami_website_id` / `umami_script_url`, D-3) is rendered by
  `base.html` only when the id is set; removing `src/ui` leaves the two settings unused.
* The Today playbook card reads `GET /api/v1/playbooks` (`brand_brain/playbooks.py`, D-4) — the
  API route stays when the UI is removed.

## What it may import (enforced by `tests/isolation/test_ui_removable.py`)

`fastapi`, `starlette`, `jinja2`, `jwt`, the standard library, `src.core.config`,
`src.core.http` (in-process client factory), `src.modules.auth.*` (token codec), and `src.ui.*`.
It never imports `sqlalchemy`, any `*.infrastructure`, repositories, or services.

## What imports it

Only `src/main.py` (two statements: `include_router(ui_router)` + `mount("/app/static",
VendorCacheControl(StaticFiles(…)))` — the wrapper adds `Cache-Control` to vendor assets).

## Removal procedure

1. `git rm -r src/ui tests/ui tests/isolation/test_ui_removable.py`
2. Delete the `src.ui` import block and the two mount lines in `src/main.py`.
3. Drop `jinja2` / `python-multipart` from `pyproject.toml` if nothing else needs them.
4. `tests/test_auth_guard.py`: remove `test_app_routes_redirect_without_session`.

No migration, no data, no ErrorCategory, no configuration key is left behind.
