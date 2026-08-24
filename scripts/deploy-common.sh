#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_DIR="/opt/pocket48-summarizer"
RELEASES_DIR="/opt/pocket48-summarizer-releases"
SLOTS_DIR="/opt/pocket48-summarizer-slots"
WORKER_LINK="/opt/pocket48-summarizer-worker"
DATA_DIR="/var/lib/pocket48-summarizer"
DATABASE="$DATA_DIR/pocket48.sqlite3"
DEPLOY_STATE_DIR="$DATA_DIR/deploy"
RUNTIME_DIR="/run/pocket48-summarizer"
ACTIVE_SLOT_FILE="$DEPLOY_STATE_DIR/active-slot"
PREVIOUS_SLOT_FILE="$DEPLOY_STATE_DIR/previous-slot"
CLIP_MAINTENANCE_FILE="$RUNTIME_DIR/clip-maintenance"
WORKER_MAINTENANCE_FILE="$RUNTIME_DIR/worker-maintenance"
WORKER_READY_FILE="$RUNTIME_DIR/worker-ready"
CLIP_OPERATION_LOCK="$RUNTIME_DIR/clip-operation.lock"
WORKER_OPERATION_LOCK="$RUNTIME_DIR/worker-operation.lock"
UPSTREAM_FILE="/etc/caddy/pocket48-upstream.caddy"

slot_port() {
  case "$1" in
    blue) echo 8000 ;;
    green) echo 8001 ;;
    *) echo "Unknown slot: $1" >&2; return 1 ;;
  esac
}

other_slot() {
  case "$1" in
    blue) echo green ;;
    green) echo blue ;;
    *) echo "Unknown slot: $1" >&2; return 1 ;;
  esac
}

read_active_slot() {
  local upstream=""
  if [[ -f "$UPSTREAM_FILE" ]]; then
    upstream="$(< "$UPSTREAM_FILE")"
    if [[ "$upstream" == *"127.0.0.1:8001"* ]]; then
      echo green
      return
    fi
    if [[ "$upstream" == *"127.0.0.1:8000"* ]]; then
      echo blue
      return
    fi
  fi
  if [[ -f "$ACTIVE_SLOT_FILE" ]]; then
    tr -d '[:space:]' < "$ACTIVE_SLOT_FILE"
  else
    echo blue
  fi
}

prepare_runtime_locks() {
  if ! install -d -m 0755 -o pocket48 -g pocket48 "$RUNTIME_DIR" \
    || ! touch "$CLIP_OPERATION_LOCK" "$WORKER_OPERATION_LOCK" \
    || ! chown pocket48:pocket48 \
      "$CLIP_OPERATION_LOCK" "$WORKER_OPERATION_LOCK" \
    || ! chmod 0660 "$CLIP_OPERATION_LOCK" "$WORKER_OPERATION_LOCK"; then
    return 1
  fi
}

activate_clip_maintenance() {
  if ! prepare_runtime_locks \
    || ! { exec 8>>"$CLIP_OPERATION_LOCK"; } \
    || ! flock -x 8; then
    return 1
  fi
  if ! install -m 0640 -o pocket48 -g pocket48 /dev/null \
    "$CLIP_MAINTENANCE_FILE"; then
    flock -u 8 || true
    return 1
  fi
  flock -u 8
}

activate_worker_maintenance() {
  if ! prepare_runtime_locks \
    || ! { exec 7>>"$WORKER_OPERATION_LOCK"; } \
    || ! flock -x 7; then
    return 1
  fi
  if ! install -m 0640 -o pocket48 -g pocket48 /dev/null \
    "$WORKER_MAINTENANCE_FILE"; then
    flock -u 7 || true
    return 1
  fi
  flock -u 7
}

write_state() {
  local path="$1"
  local value="$2"
  local temporary="$path.tmp"
  if ! printf '%s\n' "$value" > "$temporary" \
    || ! chown pocket48:pocket48 "$temporary" \
    || ! chmod 0640 "$temporary" \
    || ! mv -fT "$temporary" "$path"; then
    rm -f "$temporary"
    return 1
  fi
}

atomic_symlink() {
  local target="$1"
  local link="$2"
  local temporary="$link.next"
  if ! ln -sfn "$target" "$temporary" \
    || ! mv -fT "$temporary" "$link"; then
    rm -f "$temporary"
    return 1
  fi
}

wait_for_status_zero() {
  local table="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))
  local count
  if [[ ! -f "$DATABASE" ]]; then
    return 0
  fi
  if [[ "$(
    sqlite3 "$DATABASE" \
      "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$table';"
  )" == "0" ]]; then
    return 0
  fi
  while true; do
    count="$(
      sqlite3 "$DATABASE" \
        "SELECT COUNT(*) FROM $table WHERE status = 'running';"
    )"
    if [[ "$count" == "0" ]]; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for running rows in $table" >&2
      return 1
    fi
    sleep 2
  done
}

health_check_slot() {
  local slot="$1"
  local expected_release="${2:-}"
  local port
  local response
  if ! port="$(slot_port "$slot")"; then
    return 1
  fi
  if ! response="$(
    curl --fail --silent --show-error \
      --header "Host: p48.ruokezhang.com" \
      "http://127.0.0.1:$port/healthz"
  )"; then
    return 1
  fi
  if [[ -n "$expected_release" ]]; then
    [[ "$response" == *"\"release\":\"$expected_release\""* \
      && "$response" == *"\"worker_enabled\":false"* ]]
  fi
}

public_health_check() {
  local expected_release="$1"
  local response
  if ! response="$(
    curl --fail --silent --show-error \
      --resolve "p48.ruokezhang.com:443:127.0.0.1" \
      "https://p48.ruokezhang.com/healthz"
  )"; then
    return 1
  fi
  [[ "$response" == *"\"release\":\"$expected_release\""* \
    && "$response" == *"\"worker_enabled\":false"* ]]
}

switch_caddy_to_slot() {
  local slot="$1"
  local port
  local previous
  local temporary
  if ! port="$(slot_port "$slot")" || ! previous="$(mktemp)"; then
    return 1
  fi
  temporary="$UPSTREAM_FILE.next"
  if ! cp "$UPSTREAM_FILE" "$previous" \
    || ! printf 'reverse_proxy 127.0.0.1:%s\n' "$port" > "$temporary" \
    || ! chmod 0644 "$temporary" \
    || ! mv -fT "$temporary" "$UPSTREAM_FILE"; then
    rm -f "$previous" "$temporary"
    return 1
  fi
  if ! caddy validate --config /etc/caddy/Caddyfile >/dev/null; then
    install -m 0644 "$previous" "$UPSTREAM_FILE" || true
    rm -f "$previous"
    return 1
  fi
  if ! systemctl reload caddy.service; then
    install -m 0644 "$previous" "$UPSTREAM_FILE" || true
    systemctl reload caddy.service || true
    rm -f "$previous"
    return 1
  fi
  rm -f "$previous"
  return 0
}

switch_worker_release() {
  local release_dir="$1"
  local drain_seconds="${WORKER_DRAIN_SECONDS:-600}"
  local previous=""
  local had_previous=false
  local worker_ready=false

  if ! activate_worker_maintenance; then
    return 1
  fi
  if ! wait_for_status_zero jobs "$drain_seconds"; then
    rm -f "$WORKER_MAINTENANCE_FILE"
    return 2
  fi

  if [[ -L "$WORKER_LINK" ]]; then
    if ! previous="$(readlink -f "$WORKER_LINK")"; then
      rm -f "$WORKER_MAINTENANCE_FILE"
      return 1
    fi
    had_previous=true
  fi
  if ! rm -f "$WORKER_READY_FILE"; then
    rm -f "$WORKER_MAINTENANCE_FILE"
    return 1
  fi
  if ! systemctl stop pocket48-worker.service; then
    rm -f "$WORKER_MAINTENANCE_FILE"
    return 1
  fi
  if systemctl is-active --quiet pocket48-worker.service; then
    rm -f "$WORKER_MAINTENANCE_FILE"
    return 1
  fi
  if ! atomic_symlink "$release_dir" "$WORKER_LINK"; then
    systemctl start pocket48-worker.service || true
    rm -f "$WORKER_MAINTENANCE_FILE"
    return 1
  fi
  if ! systemctl start pocket48-worker.service; then
    if [[ "$had_previous" == true ]]; then
      if atomic_symlink "$previous" "$WORKER_LINK"; then
        systemctl start pocket48-worker.service || true
      fi
    fi
    rm -f "$WORKER_MAINTENANCE_FILE"
    return 1
  fi
  for _ in $(seq 1 30); do
    if systemctl is-active --quiet pocket48-worker.service \
      && [[ -f "$WORKER_READY_FILE" ]] \
      && [[ "$(< "$WORKER_READY_FILE")" == "$release_dir" ]]; then
      worker_ready=true
      break
    fi
    sleep 1
  done
  if [[ "$worker_ready" == true ]]; then
    for _ in $(seq 1 5); do
      sleep 2
      if ! systemctl is-active --quiet pocket48-worker.service \
        || [[ ! -f "$WORKER_READY_FILE" ]] \
        || [[ "$(< "$WORKER_READY_FILE")" != "$release_dir" ]]; then
        worker_ready=false
        break
      fi
    done
  fi
  if [[ "$worker_ready" != true ]]; then
    systemctl stop pocket48-worker.service || true
    if [[ "$had_previous" == true ]] \
      && atomic_symlink "$previous" "$WORKER_LINK"; then
      systemctl start pocket48-worker.service || true
    fi
    rm -f "$WORKER_MAINTENANCE_FILE"
    return 1
  fi
  rm -f "$WORKER_MAINTENANCE_FILE"
}
