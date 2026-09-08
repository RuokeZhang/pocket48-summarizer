#!/usr/bin/env bash
#
# Push locally-owned edit tables (video_clips, ai_cover_generations,
# ai_cover_assets, video_clip_exports) up to the ECS SQLite so that
# public visitors browsing the deployed site can see clips/covers
# produced locally. Uses INSERT OR REPLACE keyed on primary keys —
# additive, does not delete anything on the remote.
#
# Order of tables respects foreign keys:
#   video_clips -> jobs
#   ai_cover_generations -> jobs
#   ai_cover_assets -> ai_cover_generations
#   video_clip_exports -> jobs / users / ai_cover_generations / ai_cover_assets
#
# Requires: ssh key ~/.ssh/p48-ecs.pem, sqlite3 CLI both ends.
#
# Usage:
#   scripts/sync-to-ecs.sh
#   scripts/sync-to-ecs.sh --dry-run

set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
LOCAL_DB="$ROOT_DIR/data/pocket48.sqlite3"
SSH_KEY="$HOME/.ssh/p48-ecs.pem"
ECS_HOST="root@47.76.255.186"
ECS_DB="/var/lib/pocket48-summarizer/pocket48.sqlite3"

PUSH_TABLES=(
  video_clips
  ai_cover_generations
  ai_cover_assets
  video_clip_exports
)

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

if [[ ! -f "$LOCAL_DB" ]]; then
  echo "Local DB not found: $LOCAL_DB" >&2
  exit 1
fi

timestamp=$(date +%Y%m%d-%H%M%S)
push_sql_local="/tmp/pocket48-push-$timestamp.sql"
push_sql_remote="/tmp/pocket48-push-$timestamp.sql"
remote_snapshot="/tmp/pocket48-ecs-pre-push-$timestamp.sqlite3"

echo "== 1/4  Dump local edit tables =="
: > "$push_sql_local"
for t in "${PUSH_TABLES[@]}"; do
  count=$(sqlite3 "$LOCAL_DB" "SELECT COUNT(*) FROM $t" 2>/dev/null || echo "0")
  echo "  $t: $count rows"
  if $DRY_RUN; then
    continue
  fi
  sqlite3 "$LOCAL_DB" ".dump $t" \
    | grep -E '^INSERT INTO' \
    | sed 's/^INSERT INTO/INSERT OR REPLACE INTO/' \
    >> "$push_sql_local" || true
done

if $DRY_RUN; then
  echo "  [dry-run] would push, then apply on ECS"
  exit 0
fi

kept=$(wc -l < "$push_sql_local" | tr -d ' ')
if [[ "$kept" -eq 0 ]]; then
  echo "Nothing to push."
  rm -f "$push_sql_local"
  exit 0
fi

echo "== 2/4  Snapshot ECS DB (rollback safety) =="
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$ECS_HOST" \
  "sqlite3 '$ECS_DB' '.backup $remote_snapshot'"
echo "  saved -> $ECS_HOST:$remote_snapshot"

echo "== 3/4  Copy SQL to ECS =="
scp -i "$SSH_KEY" "$push_sql_local" "$ECS_HOST:$push_sql_remote"

echo "== 4/4  Apply on ECS in a single transaction =="
ssh -i "$SSH_KEY" "$ECS_HOST" \
  "sqlite3 '$ECS_DB' 'PRAGMA foreign_keys=OFF; BEGIN; .read $push_sql_remote
COMMIT;' && rm -f '$push_sql_remote'"

echo
echo "Done. Pushed $kept INSERT statements."
echo "  local dump         : $push_sql_local"
echo "  ECS pre-push copy  : $ECS_HOST:$remote_snapshot"
