#!/usr/bin/env bash
# =============================================================================
# backup_progress.sh — نسخة احتياطية قابلة للاسترجاع لتقدم العمل
#
# ينشئ git bundle (كل الفروع) + patch للتغييرات غير المُكمَّتة، ويحفظ أرشيفاً واحداً
# محلياً وفي AI Drive (/mnt/aidrive) إن كان متاحاً، ويدفع فرع العمل إلى remote إن أمكن.
#
# الاستخدام:
#   bash scripts/backup_progress.sh                       # نسخة احتياطية
#   bash scripts/backup_progress.sh --restore <archive.tar.gz> <target_dir>
#
# ملاحظة أمنية: لا يتم تضمين ~/.git-credentials أو أي ملف .env في النسخة.
# =============================================================================
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/user/webapp}"
AIDRIVE_DIR="${AIDRIVE_DIR:-/mnt/aidrive/ai_agancy_markting_backups}"
STAMP="$(date -u +%Y-%m-%d_%H%M%S)"
WORK_TMP="$(mktemp -d)"
trap 'rm -rf "$WORK_TMP"' EXIT

restore() {
  local archive="$1" target="$2"
  mkdir -p "$target"
  tar -xzf "$archive" -C "$WORK_TMP"
  local bundle patch
  bundle="$(find "$WORK_TMP" -name '*.bundle' | head -n1)"
  git clone "$bundle" "$target"
  (cd "$target" && git checkout work/implementation 2>/dev/null || true)
  patch="$(find "$WORK_TMP" -name 'uncommitted.patch' | head -n1)"
  if [[ -n "$patch" && -s "$patch" ]]; then
    (cd "$target" && git apply --3way "$patch") && echo "[restore] applied uncommitted changes"
  fi
  echo "[restore] repository restored at: $target"
}

if [[ "${1:-}" == "--restore" ]]; then
  restore "$2" "$3"
  exit 0
fi

cd "$REPO_DIR"
BRANCH="$(git branch --show-current)"
HEAD_SHA="$(git rev-parse --short HEAD)"
NAME="webapp_${BRANCH//\//-}_${HEAD_SHA}_${STAMP}"
STAGE="$WORK_TMP/$NAME"
mkdir -p "$STAGE"

git bundle create "$STAGE/repo.bundle" --all >/dev/null 2>&1
git add -A -N . >/dev/null 2>&1 || true
git diff --binary HEAD > "$STAGE/uncommitted.patch" || true
{
  echo "branch=$BRANCH"; echo "head=$HEAD_SHA"; echo "timestamp_utc=$STAMP"; git status --short
} > "$STAGE/META.txt"

ARCHIVE="$WORK_TMP/$NAME.tar.gz"
tar -czf "$ARCHIVE" -C "$WORK_TMP" "$NAME"

LOCAL_DIR="$REPO_DIR/.backups"
mkdir -p "$LOCAL_DIR" && cp "$ARCHIVE" "$LOCAL_DIR/"
echo "[backup] local: $LOCAL_DIR/$NAME.tar.gz"

if [[ -d "$(dirname "$AIDRIVE_DIR")" ]]; then
  mkdir -p "$AIDRIVE_DIR" 2>/dev/null || sudo -n mkdir -p "$AIDRIVE_DIR" 2>/dev/null || true
  if cp "$ARCHIVE" "$AIDRIVE_DIR/" 2>/dev/null || sudo -n cp "$ARCHIVE" "$AIDRIVE_DIR/" 2>/dev/null; then
    echo "[backup] aidrive: $AIDRIVE_DIR/$NAME.tar.gz"
  else
    echo "[backup] WARN: AI Drive not writable; local copy kept."
  fi
else
  echo "[backup] WARN: AI Drive not mounted; local copy kept."
fi

# Remote checkpoint (best effort; never fails the backup)
if [[ "${PUSH_REMOTE:-1}" == "1" && -n "$BRANCH" ]]; then
  if git push -q origin "$BRANCH" 2>/dev/null; then
    echo "[backup] remote: pushed $BRANCH ($HEAD_SHA)"
  else
    echo "[backup] WARN: remote push skipped/failed (offline or no credentials)."
  fi
fi
