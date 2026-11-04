#!/usr/bin/env bash
# =============================================================================
# restore_pg.sh — restore a backup made by backup_pg.sh (SPRINT-13 C-4, D-081)
#
# Usage:
#   BACKUP_PASSPHRASE=... bash scripts/restore_pg.sh <file.dump.enc> <target libpq URL>
#   BACKUP_AGE_IDENTITY=key.txt bash scripts/restore_pg.sh <file.dump.age> <target URL>
#   bash scripts/restore_pg.sh <file.dump> <target URL>                       # plain
#
# The target database must already exist and be EMPTY (the drill creates a fresh one).
# Afterwards verify with:  alembic -x database_url=<asyncpg URL> current   ⇒ expected head.
# Prints restore wall time + row counts of a few tables (ids/counts only — never customer text).
# =============================================================================
set -euo pipefail

FILE="${1:?backup file}"
TARGET="${2:?target libpq URL}"
TARGET="${TARGET/+asyncpg/}"

RAW="$(mktemp "${TMPDIR:-/tmp}/pgrestore.XXXXXX")"
trap 'rm -f "$RAW"' EXIT

case "$FILE" in
  *.age) age --decrypt --identity "${BACKUP_AGE_IDENTITY:?BACKUP_AGE_IDENTITY}" --output "$RAW" "$FILE" ;;
  *.enc) openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$FILE" -out "$RAW" -pass env:BACKUP_PASSPHRASE ;;
  *)     cp "$FILE" "$RAW" ;;
esac

START=$(date +%s)
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$TARGET" "$RAW"
END=$(date +%s)

echo "restore_pg: seconds=$((END - START)) bytes=$(stat -c %s "$FILE")"
psql "$TARGET" --no-align --tuples-only --command \
  "SELECT 'alembic_version=' || version_num FROM alembic_version
   UNION ALL SELECT 'brands=' || count(*) FROM brands
   UNION ALL SELECT 'admin_actions=' || count(*) FROM admin_actions
   UNION ALL SELECT 'ai_cost_log=' || count(*) FROM ai_cost_log;"
