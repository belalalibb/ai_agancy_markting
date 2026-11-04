#!/usr/bin/env bash
# =============================================================================
# backup_pg.sh — encrypted PostgreSQL backup (SPRINT-13 C-4, D-081)
#
#   pg_dump -Fc  ⇒  encrypt (age *or* openssl, no Python dependency)  ⇒  <out>/<stamp>.dump.<ext>
#
# Usage:
#   BACKUP_PASSPHRASE=... bash scripts/backup_pg.sh [out_dir]          # openssl aes-256-cbc -pbkdf2
#   BACKUP_AGE_RECIPIENT=age1... bash scripts/backup_pg.sh [out_dir]   # age public-key encryption
#   BACKUP_ENCRYPT=none bash scripts/backup_pg.sh [out_dir]            # plain -Fc (CI drill only)
#
# Env:
#   PG_DUMP_URL     libpq URL (postgresql://user:pass@host:port/db). Falls back to DATABASE_URL
#                   with the "+asyncpg" driver suffix stripped.
#   BACKUP_ENCRYPT  age | openssl | none  (auto: age when BACKUP_AGE_RECIPIENT is set,
#                                          else openssl when BACKUP_PASSPHRASE is set)
# Prints path, bytes, sha256 and seconds on stdout; secrets never appear in argv (`-pass env:`).
# =============================================================================
set -euo pipefail

OUT_DIR="${1:-./backups}"
STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
URL="${PG_DUMP_URL:-${DATABASE_URL:-}}"
URL="${URL/+asyncpg/}"
[[ -n "$URL" ]] || { echo "backup_pg: PG_DUMP_URL or DATABASE_URL required" >&2; exit 2; }

MODE="${BACKUP_ENCRYPT:-}"
if [[ -z "$MODE" ]]; then
  if [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then MODE=age
  elif [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then MODE=openssl
  else echo "backup_pg: set BACKUP_AGE_RECIPIENT or BACKUP_PASSPHRASE (or BACKUP_ENCRYPT=none)" >&2; exit 2
  fi
fi

mkdir -p "$OUT_DIR"
RAW="$(mktemp "${TMPDIR:-/tmp}/pgdump.XXXXXX")"
trap 'rm -f "$RAW"' EXIT

START=$(date +%s)
pg_dump --format=custom --no-owner --no-privileges --dbname="$URL" --file="$RAW"

case "$MODE" in
  age)
    OUT="$OUT_DIR/$STAMP.dump.age"
    age --recipient "$BACKUP_AGE_RECIPIENT" --output "$OUT" "$RAW" ;;
  openssl)
    OUT="$OUT_DIR/$STAMP.dump.enc"
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$RAW" -out "$OUT" -pass env:BACKUP_PASSPHRASE ;;
  none)
    OUT="$OUT_DIR/$STAMP.dump"
    cp "$RAW" "$OUT" ;;
  *) echo "backup_pg: unknown BACKUP_ENCRYPT=$MODE" >&2; exit 2 ;;
esac
chmod 600 "$OUT"
END=$(date +%s)

SHA="$(sha256sum "$OUT" | cut -d' ' -f1)"
SIZE="$(stat -c %s "$OUT")"
echo "backup_pg: file=$OUT bytes=$SIZE sha256=$SHA mode=$MODE seconds=$((END - START))"
