# Safe Removability — `ai_gateway/catalog` (D-076, D-077)

The runtime provider catalog can be removed without touching any business module. The
kill switch `FEATURE_PROVIDER_CATALOG_ENABLED=false` (default) already yields the SPRINT-11
behaviour literally (`AI_PROVIDER` / `AI_PRICE_TABLE_JSON`); deleting the code is the final step.

## Owns

| Kind | Name |
|---|---|
| Tables (migration `0015`) | `ai_providers`, `ai_models`, `ai_routing` (11 named constraints; `downgrade` drops them in reverse order) |
| Routes (11 protected, 0 public) | `/api/v1/admin/ai/providers` (GET/POST) · `/providers/{provider_id}` (PATCH/DELETE) · `/models` (GET/POST) · `/models/{model_id}` (PATCH/DELETE) · `/routing` (GET) · `/routing/{purpose}` (PUT) · `/probe` (POST) — all `PlatformAdmin` |
| Settings | `FEATURE_PROVIDER_CATALOG_ENABLED` (kill switch, default `false`) · `AI_ALLOW_LOCAL_PROVIDER_HOSTS` · `AI_CATALOG_CACHE_SECONDS` |
| Code | `src/modules/ai_gateway/catalog/**` (domain, service, admin_service, schemas, router, `infrastructure/`) · `src/core/netguard.py` (host guard helpers, also safe to keep) |
| Gateway purpose | `admin_probe` (`gateway.Purpose`) — the only purpose with `brand_id=None` |

## Touch points (the only places outside `catalog/` that know it exists)

1. `src/main.py` — mounts `admin_ai_router` behind a **local import** (one block, commented
   `SPRINT-11.1 C-1`).
2. `src/modules/ai_gateway/dependencies.py` — `get_catalog_reader`, `build_catalog_gateway` and
   the `if settings.feature_provider_catalog_enabled:` branch of `get_budget_guard`
   (imports are local to those functions). `PriceSource` stays: it is the gateway's own protocol.

Nothing in `content`, `whatsapp`, `brand_brain`, `tenancy` or `entitlements` imports the catalog
(`tests/modules/ai_gateway/catalog/test_isolation.py` enforces this).

## What stays after removal (by design)

* `ai_cost_log` rows with `purpose='admin_probe'` and `brand_id NULL` — valid finance history
  (D-081); the `brand_id` column became nullable in SPRINT-11.1 B for exactly this row kind.
* `src/core/netguard.py` — a dependency-free helper; harmless if kept.

## Removal recipe

1. Set `FEATURE_PROVIDER_CATALOG_ENABLED=false` everywhere (already the default) and deploy.
2. `alembic downgrade 0014` (drops the three tables) **or** keep the tables empty.
3. Delete `src/modules/ai_gateway/catalog/`, the `main.py` mount block and the catalog branch +
   two functions in `ai_gateway/dependencies.py`; remove the three settings from
   `src/core/config.py` and `.env.template`; drop the `admin_probe` literal from
   `gateway.Purpose` if no other caller uses it.
4. Delete `tests/modules/ai_gateway/catalog/`, `tests/modules/content/test_b5_catalog_kill_switch.py`
   and the `0015` / catalog PG tests; `PROTECTED_ROUTE_COUNT` goes back to **66**.
5. `ruff check` + the full suite stay green — no other module changes.
