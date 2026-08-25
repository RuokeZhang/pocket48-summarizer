#!/usr/bin/env bash

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=deploy-common.sh
source "$script_dir/deploy-common.sh"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this rollback as root." >&2
  exit 1
fi

clip_drain_seconds="${CLIP_DRAIN_SECONDS:-600}"
caddy_switched=false
rollback_committed=false
active_slot=""
target_slot=""

cleanup() {
  local traffic_safe_to_stop=true
  rm -f "$CLIP_MAINTENANCE_FILE"
  if [[ "$rollback_committed" != true ]]; then
    if [[ "$caddy_switched" == true && -n "$active_slot" ]]; then
      if switch_caddy_to_slot "$active_slot"; then
        write_state "$ACTIVE_SLOT_FILE" "$active_slot" || true
      else
        traffic_safe_to_stop=false
      fi
    fi
    if [[ -n "$target_slot" && "$traffic_safe_to_stop" == true ]]; then
      systemctl disable --now \
        "pocket48-web@$target_slot.service" || true
    fi
  fi
}
trap cleanup EXIT

exec 9>/run/lock/pocket48-summarizer-deploy.lock
if ! flock -n 9; then
  echo "Another Pocket48 deployment is already running." >&2
  exit 1
fi

active_slot="$(read_active_slot)"
target_slot="$(other_slot "$active_slot")"
target_link="$SLOTS_DIR/$target_slot"

if [[ ! -L "$target_link" ]]; then
  echo "Rollback slot $target_slot does not contain a release." >&2
  exit 1
fi
target_release="$(readlink -f "$target_link")"
target_commit="$(basename "$target_release")"

activate_clip_maintenance
wait_for_status_zero video_clips "$clip_drain_seconds"
wait_for_status_zero video_clip_exports "$clip_drain_seconds"

systemctl enable "pocket48-web@$target_slot.service" >/dev/null
systemctl start "pocket48-web@$target_slot.service"
for _ in $(seq 1 30); do
  if health_check_slot "$target_slot" "$target_commit"; then
    break
  fi
  sleep 1
done
if ! health_check_slot "$target_slot" "$target_commit"; then
  systemctl status "pocket48-web@$target_slot.service" --no-pager >&2 || true
  exit 1
fi

switch_caddy_to_slot "$target_slot"
caddy_switched=true
public_healthy=true
for _ in $(seq 1 5); do
  sleep 2
  if ! public_health_check "$target_commit"; then
    public_healthy=false
    break
  fi
done
if [[ "$public_healthy" != true ]]; then
  echo "Rollback health check failed; original slot remains active." >&2
  exit 1
fi

write_state "$PREVIOUS_SLOT_FILE" "$active_slot"
write_state "$ACTIVE_SLOT_FILE" "$target_slot"
rollback_committed=true
systemctl disable --now "pocket48-web@$active_slot.service" || true
rm -f "$CLIP_MAINTENANCE_FILE"

if switch_worker_release "$target_release"; then
  worker_result="updated"
else
  worker_status=$?
  if [[ "$worker_status" -eq 2 ]]; then
    worker_result="deferred (running job did not drain)"
  else
    worker_result="failed (previous worker restored when available)"
    worker_failed=true
  fi
fi

echo "Rollback complete"
echo "  active slot: $target_slot"
echo "  worker: $worker_result"
if [[ "${worker_failed:-false}" == true ]]; then
  exit 1
fi
