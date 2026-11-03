# Safe Removability — `whatsapp` lead loop (D-079) and number caps (D-082), SPRINT-12 B

Both features are additive layers over the SPRINT-09 inbox. Each has its own switch / scope so
it can be removed without touching conversations, messages, drafts or the webhook contract.

## Lead loop (D-079) — `FEATURE_LEAD_LOOP`

| Kind | Name |
|---|---|
| Columns (migration `0016`) | `wa_conversations.lead_stage` (`ck_wa_conversations_lead_stage`), `lead_stage_set_by_user_id`, `lead_stage_at` |
| Table (migration `0016`) | `lead_attributions` (`uq_lead_attributions_conversation_source`, `ck_lead_attributions_grade`, `fk_lead_attributions_brand_id`, `fk_lead_attributions_conversation_id`, `ix_lead_attributions_brand_created`) |
| Route (1 protected) | `PATCH /api/v1/brands/{brand_id}/whatsapp/conversations/{conversation_id}/lead-stage` (OWNER / MANAGER; VIEWER 403; `FEATURE_LEAD_LOOP=false` ⇒ 404 `lead_loop_disabled`) |
| Webhook | `referral.source_id` matching a succeeded `publish_jobs.external_post_id` ⇒ `lead_attributions(grade='measured')`; response field `attributed` |
| Metrics | `measured_leads_per_post`, `lead_close_rate` in `brand_brain/domain/metrics.py` (pure functions, grade `measured`) |
| Code | `src/modules/whatsapp/lead_loop.py` (`LeadLoopService`, `PublishedPostLookup` port) · repo methods `set_lead_stage`, `add_lead_attribution` |
| Settings | `FEATURE_LEAD_LOOP` (default `true`) |

Codes only (D-069): `lead_stage` and `objection_code` are enums; `signals` hold
`{"objection_code": ..}` / `{"referral_source_type": ..}` — never customer text.

Touch points: `src/main.py` binds `PgPublishedPostLookup` (publishing adapter) to
`lead_loop.get_published_post_lookup` — delete that block and the port yields `None` (no
attribution, everything else unchanged).

## Number caps (D-082) — admin-only, per `whatsapp_accounts` row

| Kind | Name |
|---|---|
| Table (migration `0016`) | `wa_number_caps` (`fk_wa_number_caps_account_id`, `ck_wa_number_caps_daily_reply_cap`) |
| Route (1 protected) | `PATCH /api/v1/admin/entitlements/wa-caps/{account_id}` (`PlatformAdmin`; 422 `wa_caps_nothing_to_change`) — on `whatsapp.router.admin_router` |
| Guard | `WhatsAppInboxService._ensure_number_cap` before every outbound send: `kill_switch` ⇒ **423 `wa_number_disabled`**; `used >= daily_reply_cap` ⇒ **429 `wa_daily_cap_reached` {cap, used, resets_in_seconds}** + `Retry-After` |
| Default | `DEFAULT_DAILY_REPLY_CAP = 300` when no row exists; inbound webhook traffic is never blocked |
| Code | repo methods `get_number_cap`, `upsert_number_cap`, `count_outbound_since`; exceptions `NumberDisabledError`, `DailyCapReachedError` |

## Removal recipe

1. Lead loop: set `FEATURE_LEAD_LOOP=false` (route 404, no attribution). Then delete
   `lead_loop.py`, the lead-stage route + schemas, the `lead_loop` branch in
   `webhook._handle_inbound`, the two metrics, the `main.py` binding; `alembic downgrade 0015`
   drops the columns and `lead_attributions` (with the rest of `0016`).
2. Caps: delete `admin_router` in `whatsapp/router.py`, `_ensure_number_cap` and its two
   call sites in `inbox.py`, the two exceptions and `to_http` mappings; drop `wa_number_caps`.
