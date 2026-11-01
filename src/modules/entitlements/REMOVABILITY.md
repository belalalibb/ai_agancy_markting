# Safe Removability — `entitlements` (D-076)

This module can be removed without touching any other business module. Everything it owns
and every point where it touches the rest of the platform is listed here.

## Owns

| Kind | Name |
|---|---|
| Tables (migration `0014`) | `brand_entitlements`, `entitlement_events` |
| Columns added by `0014` to other tables | `brands.referred_by_brand_id` (+ FK SET NULL, `ck_brands_referred_by_not_self`), `brands.learning_consent` (+ `ck_brands_learning_consent`), `wa_messages.referral` |
| Routes (4 protected, 0 public) | `POST /api/v1/admin/entitlements/activate` · `GET /api/v1/admin/entitlements/{brand_id}` · `GET /api/v1/brands/{brand_id}/entitlement` · `PATCH /api/v1/brands/{brand_id}/learning-consent` |
| Settings | `FEATURE_ENTITLEMENTS_ENFORCED` (kill switch, default `true`) · `PLATFORM_ADMIN_USER_IDS` (empty ⇒ every `/admin/*` route is 403 `platform_admin_required`) |
| Code | `src/modules/entitlements/**` |

## Touch points (the only places outside the module that know it exists)

1. `src/main.py` — mounts `entitlements_router` and `entitlements_admin_router`; logs the
   kill-switch WARNING at boot.
2. `src/modules/ai_gateway/dependencies.py::get_entitlement_limits_reader` — builds
   `PgEntitlementLimitsReader` (imported lazily) and injects it into `BudgetGuard` through
   the `EntitlementLimitsReader` protocol declared in `ai_gateway/budget.py`.

`tests/modules/entitlements/test_isolation.py` fails if any other file under `src/` imports
`src.modules.entitlements`.

Contracts this module *consumes* without importing the owner's ORM (D-016):

* `brands` / `approval_events` + `content_items` / `wa_conversations` are read with SQL
  text in `infrastructure/postgres_repository.py` (`BrandRef`, `PgCounterUsageReader`).
* `auth.dependencies.PlatformAdmin` (C-3) and `tenancy.router.Service` / `to_http` for the
  brand-scoped routes.

Related but **not owned** here (they stay when the module is removed):

* `brands.learning_consent` is read by the `BrandConsentReader` protocols declared in
  `whatsapp/consent.py` and `brand_brain/consent.py` and implemented by
  `tenancy.infrastructure.postgres_repository.PostgresBrandConsentReader` (D-080).
  Any **future cross-brand learning reader must filter `learning_consent = 'full'` only**
  — `signals_only` allows writing a brand's own signals, never reading them across brands.
* `wa_messages.referral` (CTWA) is written by `whatsapp/webhook.py` and exposed in
  `MessagePublic.referral`.

## Removal procedure

1. **Disable** — set `FEATURE_ENTITLEMENTS_ENFORCED=false` and deploy. Plan limits stop
   being enforced (the brand daily token budget and the platform USD cap in `BudgetGuard`
   are unaffected). Verify with the boot WARNING.
2. **Unmount** — in `src/main.py` delete the two `include_router` lines and the two imports;
   in `src/modules/ai_gateway/dependencies.py` make `get_entitlement_limits_reader` return
   `None` (the signature can stay). `PROTECTED_ROUTE_COUNT` drops by 4 (66 ⇒ 62).
3. **Delete** `src/modules/entitlements/`, `tests/modules/entitlements/`, and the
   `FEATURE_ENTITLEMENTS_ENFORCED` / `PLATFORM_ADMIN_USER_IDS` settings + `.env.template`
   lines (`tests/test_env_template.py` keeps both in sync). `auth.dependencies.PlatformAdmin`
   may stay for SPRINT-11.1.
4. **Schema** — `alembic downgrade 0013` drops both tables and the three columns. If the
   `learning_consent` / `referral` columns must survive, split `0014` first; they are
   independent of the entitlement tables.
5. Run `ruff check . && pytest -q --cov=src --cov-fail-under=95`.
