# Safe Removability — `ops` (D-076, SPRINT-13 Part A)

`ops` is the **Day-2 operations** module: an append-only audit log of platform-admin acts,
two DB-backed kill switches, the dead-letter view/requeue and the admin alert feed. It owns two
tables and eight `/api/v1/admin/*` routes; everything else is SQL text over other modules'
tables (same discipline as `pulse`). Removing it restores the pre-SPRINT-13 behaviour: no audit
rows, every switch read from `Settings` only, failed publish jobs stay invisible to admins.

## Owns

| Kind | Name |
|---|---|
| Tables (migration `0017_day2_ops`, shared with B/C columns) | `admin_actions` (`ix_admin_actions_target_at`) · `platform_flags` (`key` PK) · column `brands.suspended_at` |
| Routes (8 protected, 0 public; non-admin ⇒ **404**) | `GET /api/v1/admin/actions` · `POST /api/v1/admin/brands/{brand_id}/suspend` · `POST /api/v1/admin/brands/{brand_id}/unsuspend` · `GET /api/v1/admin/flags` · `PUT /api/v1/admin/flags/{key}` · `GET /api/v1/admin/publishing/dead-letter` · `POST /api/v1/admin/publishing/jobs/{job_id}/requeue` · `GET /api/v1/admin/alerts?kind=` |
| Flags (`platform_flags.key`) | `platform_readonly` · `feature_pulse` · `feature_lead_loop` — DB row first, `Settings` fallback, 30 s in-process cache (`PLATFORM_FLAGS`) |
| Codes (D-069) | `brand_suspended` (403) · `platform_readonly` (503 + `Retry-After: 300`) · `job_not_failed` (409) · `publish_job_not_found` (404) · `flag_unknown` (404) · event `requeued_by_admin` (D-055) |
| Code | `src/modules/ops/**` (`domain.py`, `exceptions.py`, `service.py`, `guards.py`, `audit.py`, `router.py`, `infrastructure/postgres_repository.py`) |

Audit rows hold ids / codes / counts only — never customer text (D-069). The row is written
on the **request session** of the act it records, so a failed act leaves zero rows (A-1).

## Touch points (the only places outside `ops/` that know it exists)

1. `src/main.py` — `platform_readonly_guard` on the `/api/v1` router, `require_brand_not_suspended`
   on every brand-scoped router (`suspended = [Depends(...)]`), `feature_flag(...)` overriding the
   consumer-owned ports `pulse.router.get_pulse_enabled` and
   `whatsapp.lead_loop.get_lead_loop_enabled` (D-016), and `include_router(ops_admin_router)`.
2. Audit hooks (`src.modules.ops.audit.Audit`) in `ai_gateway/catalog/router.py`
   (provider/model create·update·delete, routing), `entitlements/router.py` (activate) and
   `whatsapp/router.py` (wa-caps). Each is one `await audit.record(...)` after the act.
3. `tenancy`: `Brand.suspended_at` / `BrandOut.suspended` (read exposure only, §3-9).
4. `src/ui/router.py`: tabs `audit` / `dead_letter` and the suspend/unsuspend buttons — pure
   `ApiClient` calls, no import of `ops`.
5. `tests/conftest.py` overrides `get_ops_repository` / `get_ops_service` / `get_admin_audit`
   with `tests.fakes.FakeOpsRepository`.

## Removal procedure

1. `git rm -r src/modules/ops tests/modules/ops`.
2. `src/main.py`: delete the two `ops` imports, the `api_v1` `dependencies=` argument, the
   `suspended` list, the two `feature_flag` overrides and the `ops_admin_router` mount.
3. Delete the `Audit` parameter + `audit.record(...)` lines in the three routers above.
4. `tests/test_auth_guard.py`: `PROTECTED_ROUTE_COUNT -= 8`; `tests/conftest.py`: drop the
   three overrides; `tests/fakes.py`: drop `FakeOpsRepository`.
5. UI: remove the two tabs from `ADMIN_TABS`, the `audit` / `dead_letter` branches and the
   suspension card; delete the `admin.tab_audit` … `flag.*` keys in `ar.json`.
6. Schema: `alembic downgrade 0016` drops both tables and `brands.suspended_at` together with
   the B/C columns of `0017` (one migration — the B/C parts live in `whatsapp`/`brand_brain`/
   `brand_exports`); keep `0017` if those stay and only drop the two tables by hand.
