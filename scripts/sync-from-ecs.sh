#!/usr/bin/env bash
#
# Pull processing state (jobs, subtitles, summaries, danmaku, etc.)
# from ECS into the local SQLite while preserving locally-owned edit
# tables (video_clips, video_clip_exports, ai_cover_generations,
# ai_cover_assets).
#
# Requires: ssh key ~/.ssh/p48-ecs.pem, sqlite3 CLI.
#
# Usage:
#   scripts/sync-from-ecs.sh          # run the sync
#   scripts/sync-from-ecs.sh --dry-run # show what would happen

set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
LOCAL_DB="$ROOT_DIR/data/pocket48.sqlite3"
SSH_KEY="$HOME/.ssh/p48-ecs.pem"
ECS_HOST="root@47.76.255.186"
ECS_DB="/var/lib/pocket48-summarizer/pocket48.sqlite3"

PRESERVE_TABLES=(
  video_clips
  ai_cover_generations
  ai_cover_assets
  video_clip_exports
)

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

timestamp=$(date +%Y%m%d-%H%M%S)
snapshot_remote="/tmp/pocket48-remote-snapshot-$timestamp.sqlite3"
snapshot_local="$LOCAL_DB.pre-sync-$timestamp"
preserve_sql="/tmp/pocket48-local-preserve-$timestamp.sql"

echo "== 1/5  Snapshot ECS DB via SQLite backup API =="
if $DRY_RUN; then
  echo "  [dry-run] would ssh $ECS_HOST 'sqlite3 $ECS_DB \".backup $snapshot_remote\"'"
else
  ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$ECS_HOST" \
    "sqlite3 '$ECS_DB' '.backup $snapshot_remote'"
fi

echo "== 2/5  Dump local edit tables to $preserve_sql =="
if [[ -f "$LOCAL_DB" ]]; then
  if $DRY_RUN; then
    for t in "${PRESERVE_TABLES[@]}"; do
      count=$(sqlite3 "$LOCAL_DB" "SELECT COUNT(*) FROM $t" 2>/dev/null || echo "0")
      echo "  [dry-run] would dump $t ($count rows)"
    done
  else
    : > "$preserve_sql"
    for t in "${PRESERVE_TABLES[@]}"; do
      sqlite3 "$LOCAL_DB" ".dump $t" \
        | grep -E '^INSERT INTO' \
        | sed 's/^INSERT INTO/INSERT OR REPLACE INTO/' \
        >> "$preserve_sql" || true
    done
    kept=$(wc -l < "$preserve_sql" | tr -d ' ')
    echo "  preserved $kept INSERT statements"
  fi
else
  echo "  no local DB yet — fresh install, nothing to preserve"
fi

echo "== 3/5  Snapshot current local DB to $snapshot_local =="
if [[ -f "$LOCAL_DB" ]]; then
  if $DRY_RUN; then
    echo "  [dry-run] would cp $LOCAL_DB $snapshot_local"
  else
    cp "$LOCAL_DB" "$snapshot_local"
  fi
else
  mkdir -p "$(dirname "$LOCAL_DB")"
fi

echo "== 4/5  Copy remote snapshot down and swap in =="
if $DRY_RUN; then
  echo "  [dry-run] would scp $ECS_HOST:$snapshot_remote -> $LOCAL_DB"
else
  scp -i "$SSH_KEY" "$ECS_HOST:$snapshot_remote" "$LOCAL_DB"
  ssh -i "$SSH_KEY" "$ECS_HOST" "rm -f $snapshot_remote"
fi

echo "== 5/5  Replay preserved local edit rows =="
if [[ -s "${preserve_sql}" && "$DRY_RUN" == "false" ]]; then
  # PRAGMA foreign_keys=OFF so out-of-order replays don't complain;
  # the CREATE statements from ECS already have the FK definitions.
  sqlite3 "$LOCAL_DB" "PRAGMA foreign_keys=OFF; BEGIN; $(cat "$preserve_sql") COMMIT;"
  echo "  restored"
else
  echo "  nothing to restore"
fi

echo
echo "Done."
echo "  local DB      : $LOCAL_DB"
if ! $DRY_RUN; then
  echo "  rollback copy : $snapshot_local"
  echo "  preserve dump : $preserve_sql"
fi
