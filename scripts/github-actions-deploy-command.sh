#!/usr/bin/env bash

set -euo pipefail

commit="${SSH_ORIGINAL_COMMAND:-${1:-}}"
repository="/opt/pocket48-summarizer"
database="/var/lib/pocket48-summarizer/pocket48.sqlite3"
public_url="https://p48.ruokezhang.com"

if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "A full lowercase Git commit SHA is required." >&2
  exit 2
fi

cd "$repository"
git fetch --prune origin main
git cat-file -e "$commit^{commit}"
git merge-base --is-ancestor "$commit" origin/main
git checkout main
git merge --ff-only "$commit"
test "$(git rev-parse HEAD)" = "$commit"

CLIP_DRAIN_SECONDS=600 WORKER_DRAIN_SECONDS=600 \
  ./scripts/deploy-release.sh "$commit"

health="$(
  curl --fail --silent --show-error --retry 5 --retry-delay 2 \
    "$public_url/healthz"
)"
grep -F "\"release\":\"$commit\"" <<< "$health"

test "$(
  sqlite3 "$database" \
    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='video_clip_exports';"
)" = "1"
test "$(
  sqlite3 "$database" \
    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='clip_boundary_suggestions';"
)" = "1"
test "$(
  sqlite3 "$database" "
    SELECT COUNT(*)
    FROM video_clips AS legacy
    LEFT JOIN video_clip_exports AS exported
      ON exported.job_id = legacy.job_id
     AND exported.request_id = 'legacy:' || legacy.timeline_index
    WHERE exported.id IS NULL;
  "
)" = "0"

echo "Production deployment passed for $commit"
