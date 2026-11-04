# Safe Removability — `lifecycle` (D-076, SPRINT-13 Part C, D-081)

`lifecycle` is the **data lifecycle** module: brand export (ZIP), brand delete and the
retention sweep. It owns one table (`brand_exports`, created in `0017_day2_ops`) and three
OWNER-only routes; everything else is SQL `text()` over other modules' tables (same discipline
as `pulse` / `ops`). Removing it restores the pre-SPRINT-13 behaviour: no self-service export,
no self-service delete, no retention sweep.

## Owns

| Kind | Name |
|---|---|
| Table (migration `0017_day2_ops`) | `brand_exports` (`fk_brand_exports_brand_id` CASCADE, `ck_brand_exports_status`, `ix_brand_exports_brand_created`) |
| Routes (3 protected, 0 public; OWNER + `CurrentUserFresh`) | `POST /api/v1/brands/{brand_id}/export` (202) · `GET /api/v1/brands/{brand_id}/export/{export_id}` (JSON status or ZIP) · `DELETE /api/v1/brands/{brand_id}` (204) |
| Codes (D-069) | `export_too_large` (413) · `export_not_found` (404) · `confirmation_mismatch` (422) · audit actions `brand_deleted` · `retention_sweep` |
| Settings | `RETENTION_DRY_RUN` (default **true**) · `RETENTION_INTERVAL_SECONDS` (86 400) · `RETENTION_MONTHS` (12) · `RETENTION_MONTHS_AGENCY` (24) · `RETENTION_EXPORT_HOURS` (24) |
| Code | `src/modules/lifecycle/**` (`domain.py`, `exceptions.py`, `service.py`, `router.py`, `scheduler_step.py`, `infrastructure/postgres_repository.py`) |

Exports never contain tokens, secrets, hashes, consent or shared texts (`_DROP_FRAGMENTS` in
the explicit `EXPORT_COLUMNS` allowlist in `domain.py` (SPRINT-13.1 F-4) + `FORBIDDEN_FRAGMENTS` checked on **key names only** in `build_zip`; any other export error ⇒ `export_failed`). The delete audit row
carries `sha256(brand_id)` only. The sweep never touches `ai_cost_log`, `entitlement_events`,
`approval_events` or `admin_actions`.

## Touch points (the only places outside `lifecycle/` that know it exists)

1. `src/main.py` — `include_router(lifecycle_router)` (deliberately **not** under `suspended`,
   SPRINT-13.1 F-4: a suspended brand's OWNER must still export / delete — D-081) and
   `retention_step(settings)` in the scheduler `extra_steps`.
2. `src/core/config.py` — the five `retention_*` settings (harmless if left).
3. `src/ui/router.py` + `src/ui/templates/settings.html` — the danger zone
   (`POST /app/b/{id}/settings/export`, `POST /app/b/{id}/settings/delete`) — pure `ApiClient`
   calls, no import of `lifecycle`; `ar.json` keys `settings.danger_*`, `settings.deleted`,
   `error.export_too_large`, `error.export_not_found`, `error.confirmation_mismatch`,
   `error.export_failed`.
4. `tests/conftest.py` overrides `get_lifecycle_repository` / `get_unsubscriber` with
   `tests.fakes.FakeLifecycleRepository`.
5. `ops/RESTORE_DRILLS.md`, `scripts/backup_pg.sh`, `scripts/restore_pg.sh` and the CI job
   `restore-drill` are independent of this module (they only need `alembic` + `pg_dump`).

## Removal procedure

1. `git rm -r src/modules/lifecycle tests/modules/lifecycle tests/ui/test_ui_lifecycle.py tests/integration/test_sprint13_c_pg.py` (+ the F-4 cases in `tests/integration/test_sprint13_1_pg.py`).
2. `src/main.py`: delete the two `lifecycle` imports, the `include_router(lifecycle_router, …)`
   line and `retention_step(settings)` from `extra_steps`.
3. `src/ui`: remove the `data-card="danger"` block from `settings.html`, the two handlers in
   `router.py` and the keys above from `ar.json`; `tests/conftest.py`: drop the overrides and
   `FakeLifecycleRepository` from `tests/fakes.py`.
4. `tests/test_auth_guard.py` / `tests/modules/content/test_router.py`: `PROTECTED_ROUTE_COUNT`
   96 ⇒ 93.
5. A follow-up migration may `DROP TABLE brand_exports` (0017 itself is never edited).
