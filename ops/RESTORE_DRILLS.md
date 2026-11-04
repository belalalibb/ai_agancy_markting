# Restore drills — PostgreSQL backups (SPRINT-13 C-4, D-081)

A backup that was never restored is a hope, not a backup. Every drill is one line in the log.

## Procedure

1. **Backup** (production or staging):
   `BACKUP_PASSPHRASE=… bash scripts/backup_pg.sh /var/backups/aigos`
   (`pg_dump -Fc` ⇒ `openssl enc -aes-256-cbc -pbkdf2`; or `BACKUP_AGE_RECIPIENT=age1…` ⇒ `age`.
   No Python dependency; the passphrase is read from the environment, never from argv.)
2. **Fresh target**: `createdb aigos_drill` — the target must be EMPTY (the restore never merges).
3. **Restore**: `BACKUP_PASSPHRASE=… bash scripts/restore_pg.sh <file> postgresql://…/aigos_drill`
   — prints `restore_pg: seconds=N bytes=M` plus `alembic_version`, `brands`, `admin_actions`,
   `ai_cost_log` counts (ids/counts only — never customer text, D-069).
4. **Verify**: `alembic -x database_url=postgresql+asyncpg://…/aigos_drill current` ⇒ the
   expected head (`0017` for SPRINT-13); row counts before/after must be identical.
5. **Record** a line below and sign it. Drop the drill database.

The CI job **`restore-drill`** (`.github/workflows/ci.yml`) performs this drill on every push:
`alembic upgrade head` on a pgvector PG service, seed one tenant/brand + one `admin_actions`
row, `backup_pg.sh` (plain and openssl-encrypted), `restore_pg.sh` into a fresh database,
assert `alembic current == 0017` and identical row counts, and print `restore_pg: seconds=…`
— that number is the "restore time" column.

## Backup retention

Encrypted dumps are kept **35 days** (rolling). The passphrase / age identity lives in the
secrets manager — never in the repo (`gitleaks` gate) nor in `.env.template`.

## Drill log

| Date (UTC) | Source | Dump size | Restore time | Rows before ⇒ after (brands / admin_actions / ai_cost_log) | `alembic current` | Signature |
|---|---|---|---|---|---|---|
| 2026-11-04 | CI `restore-drill` run [41767754885](https://github.com/belalalibb/ai_agancy_markting/actions/runs/41767754885/job/126370518099) — sample DB after `alembic upgrade head` on `pgvector/pgvector:pg16`, `pg_dump` 16.15, openssl-encrypted dump | 109,232 B (`sha256 8661f2f8…edaef9f`) | `restore_pg: seconds=0` (< 1 s; whole job 40 s incl. apt + pip) | 1 / 1 / 0 ⇒ 1 / 1 / 0 | `0017 (head)` | Implementation Executor (SPRINT-13 C) |
