# Safe Removability — `pulse` (D-076, SPRINT-12 B-3)

Pulse is a **read-only SQL observer**: eight checks over other modules' tables, one table of
its own, two routes and one scheduler step. The kill switch `FEATURE_PULSE=false` yields the
pre-SPRINT-12 behaviour literally (no routes mounted, no step scheduled, no row written);
deleting the code is the final step.

## Owns

| Kind | Name |
|---|---|
| Table (migration `0016`, shared with lead loop / WA caps) | `pulse_alerts` (`ck_pulse_alerts_kind`, `ck_pulse_alerts_severity`, `fk_pulse_alerts_brand_id`, partial index `ix_pulse_alerts_brand_open`) |
| Routes (2 protected, 0 public) | `GET /api/v1/brands/{brand_id}/pulse/alerts?open=true` (all brand roles) · `POST /api/v1/brands/{brand_id}/pulse/alerts/{alert_id}/ack` (OWNER / MANAGER) |
| Settings | `FEATURE_PULSE` (kill switch, default `true`) · `PULSE_INTERVAL_SECONDS` (default 900) |
| Code | `src/modules/pulse/**` (`domain.py`, `service.py`, `router.py`, `scheduler_step.py`, `infrastructure/postgres_repository.py`) |
| Checks (SQL only, no AI, no HTTP) | `wa_window_closing` · `wa_slow_replies` · `no_publish_7d` · `publish_failed_24h` · `meta_token_expiring` · `plan_counter_80pct` · `wa_quality_degraded` · `wa_daily_cap_80pct` |

Idempotency: an alert is created only when no **open** alert with the same
`(brand_id, kind, payload->>'ref')` exists; acknowledging re-arms the check. Payloads hold
ids / counts / dates only — never customer text (D-069).

## Touch points (the only places outside `pulse/` that know it exists)

1. `src/main.py` — `pulse_scheduler_step()` (local imports) adds `Pulse.run(now)` as an
   `extra_steps` entry of the publish scheduler, and `create_app` mounts `pulse.router`
   behind `if settings.feature_pulse:`.
2. Check (6) reads plan counters through the consumer-owned port
   `pulse.domain.PlanCounterUsageReader` (D-016) and `pulse_step(flag_reader=…)` (SPRINT-13.1
   F-6: `main.py` binds `ops.guards.flag_active(…, "feature_pulse")` so a DB flag flipped off
   skips the cycle — no `ops` import inside `pulse`); `src/main.py` binds the entitlements adapter
   `PgPlanCounterUsageReader`. **Entitlements never imports pulse** — the adapter returns
   duck-typed `PlanCounterUsageRow`s.

Nothing in `whatsapp`, `publishing`, `social_connect`, `brand_brain`, `tenancy` or
`entitlements` imports `src.modules.pulse` (`tests/isolation/test_pulse_removable.py` enforces
this).

## What stays after removal (by design)

* Nothing. `pulse_alerts` is dropped by `alembic downgrade 0015` together with the rest of
  `0016`; keep it empty if the migration stays.

## Removal recipe

1. Set `FEATURE_PULSE=false` everywhere and deploy — routes answer 404, the step is gone.
2. Delete `src/modules/pulse/`, `pulse_scheduler_step()` and the mount block in
   `src/main.py`, `PgPlanCounterUsageReader` / `plan_counter_usage` / `PlanCounterUsageRow`
   in `entitlements` (they have no other consumer), the two settings in `src/core/config.py`
   and `.env.template`, and `tests/isolation/test_pulse_removable.py` + `tests/modules/pulse/`.
3. Either `alembic downgrade 0015` (drops `pulse_alerts`, `lead_attributions`,
   `wa_number_caps` and the lead-stage / share-token columns — only if the lead loop and WA
   caps go too) **or** write a `0017` that drops `pulse_alerts` alone.
