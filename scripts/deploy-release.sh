#!/usr/bin/env bash

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=deploy-common.sh
source "$script_dir/deploy-common.sh"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this deployer as root." >&2
  exit 1
fi

release_ref="${1:-origin/main}"
clip_drain_seconds="${CLIP_DRAIN_SECONDS:-600}"
temporary_release=""
release_building=false
active_slot=""
standby_slot=""
standby_link_changed=false
standby_had_previous=false
standby_previous_target=""
standby_env=""
standby_env_backup=""
caddy_switched=false
deployment_committed=false

cleanup() {
  local traffic_safe_to_stop=true
  rm -f "$CLIP_MAINTENANCE_FILE"
  if [[ "$deployment_committed" != true ]]; then
    if [[ "$caddy_switched" == true && -n "$active_slot" ]]; then
      if switch_caddy_to_slot "$active_slot"; then
        write_state "$ACTIVE_SLOT_FILE" "$active_slot" || true
      else
        traffic_safe_to_stop=false
      fi
    fi
    if [[ "$standby_link_changed" == true
      && "$traffic_safe_to_stop" == true ]]; then
      systemctl disable --now \
        "pocket48-web@$standby_slot.service" || true
      if [[ "$standby_had_previous" == true ]]; then
        atomic_symlink "$standby_previous_target" \
          "$SLOTS_DIR/$standby_slot"
      else
        rm -f "$SLOTS_DIR/$standby_slot"
      fi
      if [[ -n "$standby_env_backup" && -f "$standby_env_backup" ]]; then
        install -m 0640 -o root -g pocket48 \
          "$standby_env_backup" "$standby_env"
      fi
    fi
  fi
  rm -f "$standby_env_backup"
  if [[ -n "$temporary_release" && -d "$temporary_release" ]]; then
    rm -rf -- "$temporary_release"
  fi
  if [[ "$release_building" == true && -d "${release_dir:-}" ]]; then
    rm -rf -- "$release_dir"
  fi
}
trap cleanup EXIT

exec 9>/run/lock/pocket48-summarizer-deploy.lock
if ! flock -n 9; then
  echo "Another Pocket48 deployment is already running." >&2
  exit 1
fi

git -C "$REPOSITORY_DIR" fetch --prune origin
commit="$(git -C "$REPOSITORY_DIR" rev-parse "$release_ref^{commit}")"
release_dir="$RELEASES_DIR/$commit"
short_commit="${commit:0:12}"

install -d -m 0755 "$RELEASES_DIR" "$SLOTS_DIR"
install -d -m 0750 -o pocket48 -g pocket48 "$DEPLOY_STATE_DIR"

release_valid=false
if [[ -x "$release_dir/.venv/bin/pocket48-summarizer" ]]; then
  shebang=""
  IFS= read -r shebang \
    < "$release_dir/.venv/bin/pocket48-summarizer" || true
  if [[ "$shebang" == "#!$release_dir/.venv/bin/python" ]]; then
    release_valid=true
  fi
fi
if [[ "$release_valid" != true ]]; then
  rm -rf -- "$release_dir"
  temporary_release="$(mktemp -d "$RELEASES_DIR/.release-$short_commit.XXXXXX")"
  git -C "$REPOSITORY_DIR" archive "$commit" \
    | tar -x -C "$temporary_release"
  release_building=true
  mv "$temporary_release" "$release_dir"
  temporary_release=""
  python3 -m venv "$release_dir/.venv"
  "$release_dir/.venv/bin/python" -m pip install --upgrade pip
  "$release_dir/.venv/bin/python" -m pip install \
    -r "$release_dir/requirements.lock"
  "$release_dir/.venv/bin/python" -m pip install \
    --no-deps "$release_dir"
  chown root:pocket48 "$release_dir"
  chmod 0750 "$release_dir"
  release_building=false
fi
chown root:pocket48 "$release_dir"
chmod 0750 "$release_dir"

if [[ -f /etc/pocket48-summarizer/app.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /etc/pocket48-summarizer/app.env
  set +a
fi
ensure_clip_overlay_font_packages
verify_clip_overlay_dependencies

active_slot="$(read_active_slot)"
standby_slot="$(other_slot "$active_slot")"

activate_clip_maintenance
wait_for_status_zero video_clips "$clip_drain_seconds"
wait_for_status_zero video_clip_exports "$clip_drain_seconds"
wait_for_status_zero \
  ai_cover_generations "$clip_drain_seconds" "'queued','running'"

systemctl disable --now "pocket48-web@$standby_slot.service" || true
if [[ -L "$SLOTS_DIR/$standby_slot" ]]; then
  standby_had_previous=true
  standby_previous_target="$(readlink -f "$SLOTS_DIR/$standby_slot")"
fi
atomic_symlink "$release_dir" "$SLOTS_DIR/$standby_slot"
standby_link_changed=true
standby_env="/etc/pocket48-summarizer/web-$standby_slot.env"
if [[ -f "$standby_env" ]]; then
  standby_env_backup="$(mktemp)"
  cp "$standby_env" "$standby_env_backup"
fi
printf \
  'APP_PORT=%s\nAPP_RELEASE=%s\nENABLE_WORKER=false\nENABLE_CLIPPER=true\n' \
  "$(slot_port "$standby_slot")" "$commit" > "$standby_env.next"
chown root:pocket48 "$standby_env.next"
chmod 0640 "$standby_env.next"
mv -fT "$standby_env.next" "$standby_env"
systemctl enable "pocket48-web@$standby_slot.service" >/dev/null
systemctl start "pocket48-web@$standby_slot.service"

for _ in $(seq 1 30); do
  if health_check_slot "$standby_slot" "$commit"; then
    break
  fi
  sleep 1
done
if ! health_check_slot "$standby_slot" "$commit"; then
  systemctl status "pocket48-web@$standby_slot.service" --no-pager >&2 || true
  exit 1
fi

switch_caddy_to_slot "$standby_slot"
caddy_switched=true
public_healthy=true
for _ in $(seq 1 5); do
  sleep 2
  if ! public_health_check "$commit"; then
    public_healthy=false
    break
  fi
done
if [[ "$public_healthy" != true ]]; then
  echo "Public health check failed; traffic was rolled back." >&2
  exit 1
fi

write_state "$PREVIOUS_SLOT_FILE" "$active_slot"
write_state "$ACTIVE_SLOT_FILE" "$standby_slot"
deployment_committed=true
standby_link_changed=false
systemctl disable --now "pocket48-web@$active_slot.service" || true
systemctl disable --now pocket48-summarizer.service || true
rm -f "$CLIP_MAINTENANCE_FILE"

if switch_worker_release "$release_dir"; then
  systemctl enable pocket48-worker.service >/dev/null
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

echo "Deployment complete"
echo "  release: $short_commit"
echo "  active slot: $standby_slot"
echo "  worker: $worker_result"
if [[ "${worker_failed:-false}" == true ]]; then
  exit 1
fi
