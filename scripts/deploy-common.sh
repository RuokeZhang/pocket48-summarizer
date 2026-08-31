#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_DIR="/opt/pocket48-summarizer"
RELEASES_DIR="/opt/pocket48-summarizer-releases"
SLOTS_DIR="/opt/pocket48-summarizer-slots"
WORKER_LINK="/opt/pocket48-summarizer-worker"
VOICE_MONITOR_LINK="/opt/pocket48-summarizer-voice-monitor"
DATA_DIR="/var/lib/pocket48-summarizer"
DATABASE="$DATA_DIR/pocket48.sqlite3"
DEPLOY_STATE_DIR="$DATA_DIR/deploy"
RUNTIME_DIR="/run/pocket48-summarizer"
ACTIVE_SLOT_FILE="$DEPLOY_STATE_DIR/active-slot"
PREVIOUS_SLOT_FILE="$DEPLOY_STATE_DIR/previous-slot"
CLIP_MAINTENANCE_FILE="$RUNTIME_DIR/clip-maintenance"
WORKER_MAINTENANCE_FILE="$RUNTIME_DIR/worker-maintenance"
WORKER_READY_FILE="$RUNTIME_DIR/worker-ready"
VOICE_MONITOR_READY_FILE="$RUNTIME_DIR/room-voice-monitor-ready"
VOICE_MONITOR_STATUS_FILE="$RUNTIME_DIR/room-voice-monitor-status.json"
VOICE_MONITOR_WANG_RUIQI_READY_FILE="$RUNTIME_DIR/room-voice-monitor-wang-ruiqi-ready"
VOICE_MONITOR_WANG_RUIQI_STATUS_FILE="$RUNTIME_DIR/room-voice-monitor-wang-ruiqi-status.json"
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

verify_clip_overlay_dependencies() {
  local python_executable="${1:-python3}"
  local font_name="${CLIP_FONT_NAME:-Noto Sans CJK SC}"
  local required_font
  local matched_font
  if ! command -v ffprobe >/dev/null 2>&1; then
    echo "ffprobe is required for clip overlays." >&2
    return 1
  fi
  if ! ffmpeg -nostdin -hide_banner -filters 2>/dev/null \
    | awk '$2 == "ass" { found = 1 } END { exit !found }'; then
    echo "FFmpeg must include the libass 'ass' filter." >&2
    return 1
  fi
  if ! command -v fc-match >/dev/null 2>&1; then
    echo "fontconfig is required to validate the clip font." >&2
    return 1
  fi
  for required_font in \
    "$font_name" \
    "Noto Sans CJK SC" \
    "Noto Serif CJK SC" \
    "LXGW WenKai"; do
    matched_font="$(
      fc-match --format='%{family}\n' "$required_font" | head -n 1
    )"
    if [[ "$matched_font" != *"$required_font"* ]]; then
      echo "Required clip font '$required_font' is unavailable (matched '$matched_font')." >&2
      return 1
    fi
  done
  # fc-match always answers with something, so it cannot prove emoji coverage.
  # Danmaku are full of emoji and libass draws nothing for an uncovered
  # codepoint, so assert that some font actually carries the glyph.
  if ! fc-list ':charset=1f389' family | grep -q .; then
    echo "No installed font covers emoji; clip danmaku would lose them." >&2
    return 1
  fi
  if ! "$python_executable" \
    "$REPOSITORY_DIR/scripts/verify-color-emoji.py"; then
    echo "Pillow cannot render the required full-color emoji sequences." >&2
    return 1
  fi
}

CLIP_OVERLAY_FONT_PACKAGES=(
  fontconfig
  fonts-lxgw-wenkai
  fonts-noto-cjk
  fonts-noto-color-emoji
)
CLIP_OVERLAY_FONT_INSTALL_SPECS=(
  fontconfig
  fonts-lxgw-wenkai
  fonts-noto-cjk
  "fonts-noto-color-emoji=2.042-1"
)

ensure_clip_overlay_font_packages() {
  local installed_packages
  local emoji_version
  installed_packages="$(
    dpkg-query -W -f='${Status}\n' \
      "${CLIP_OVERLAY_FONT_PACKAGES[@]}" 2>/dev/null \
      | grep -c '^install ok installed$' \
      || true
  )"
  emoji_version="$(
    dpkg-query -W -f='${Version}' fonts-noto-color-emoji 2>/dev/null \
      || true
  )"
  if [[ "$installed_packages" == "${#CLIP_OVERLAY_FONT_PACKAGES[@]}" \
    && "$emoji_version" == "2.042-1" ]]; then
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --allow-downgrades \
    "${CLIP_OVERLAY_FONT_INSTALL_SPECS[@]}"
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

install_release_units() {
  local release_dir="$1"
  local source
  local destination
  for source in \
    deploy/systemd/pocket48-web@.service \
    deploy/systemd/pocket48-worker.service; do
    if [[ -f "$release_dir/$source" ]]; then
      destination="/etc/systemd/system/${source##*/}"
      install -m 0644 "$release_dir/$source" "$destination"
    fi
  done
  systemctl daemon-reload
}

restore_voice_monitor_unit() {
  local backup="$1"
  local had_previous="$2"
  if [[ "$had_previous" == true ]]; then
    install -m 0644 "$backup" \
      /etc/systemd/system/pocket48-voice-monitor.service
  else
    rm -f /etc/systemd/system/pocket48-voice-monitor.service
  fi
  systemctl daemon-reload
}

voice_monitor_expects_wang_ruiqi() {
  local release_dir="$1"
  local target_env="$release_dir/deploy/room-voice-target.env"
  [[ -f "$target_env" ]] \
    && grep -Fq '"id":"wang-ruiqi"' "$target_env"
}

voice_monitor_release_ready() {
  local release_dir="$1"
  local ready_files=("$VOICE_MONITOR_READY_FILE")
  local status_files=("$VOICE_MONITOR_STATUS_FILE")
  local path
  if voice_monitor_expects_wang_ruiqi "$release_dir"; then
    ready_files+=("$VOICE_MONITOR_WANG_RUIQI_READY_FILE")
    status_files+=("$VOICE_MONITOR_WANG_RUIQI_STATUS_FILE")
  fi
  for path in "${ready_files[@]}"; do
    if [[ ! -f "$path" || "$(< "$path")" != "$release_dir" ]]; then
      return 1
    fi
  done
  for path in "${status_files[@]}"; do
    if [[ ! -f "$path" ]] \
      || grep -Eq \
        '"error_code":[[:space:]]*"configuration_error"' \
        "$path" 2>/dev/null; then
      return 1
    fi
  done
}

wait_for_status_zero() {
  local table="$1"
  local timeout_seconds="$2"
  local statuses="${3:-'running'}"
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
        "SELECT COUNT(*) FROM $table WHERE status IN ($statuses);"
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
  if ! wait_for_status_zero jobs "$drain_seconds" \
    || ! wait_for_status_zero \
      subtitle_translation_requests "$drain_seconds"; then
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

switch_voice_monitor_release() {
  local release_dir
  release_dir="$(readlink -f "$1")"
  local previous=""
  local had_previous=false
  local monitor_ready=false
  local unit_backup=""
  local had_previous_unit=false
  local candidate_unit="$release_dir/deploy/systemd/pocket48-voice-monitor.service"

  if [[ ! -x "$release_dir/.venv/bin/pocket48-voice-monitor" \
    || ! -f "$candidate_unit" ]]; then
    systemctl disable --now pocket48-voice-monitor.service || true
    rm -f \
      "$VOICE_MONITOR_READY_FILE" \
      "$VOICE_MONITOR_WANG_RUIQI_READY_FILE"
    rm -f "$VOICE_MONITOR_LINK"
    return 3
  fi
  if [[ -L "$VOICE_MONITOR_LINK" ]]; then
    if ! previous="$(readlink -f "$VOICE_MONITOR_LINK")"; then
      return 1
    fi
    if [[ -x "$previous/.venv/bin/pocket48-voice-monitor" ]]; then
      had_previous=true
    else
      rm -f "$VOICE_MONITOR_LINK"
    fi
  fi
  unit_backup="$(mktemp)"
  if [[ -f /etc/systemd/system/pocket48-voice-monitor.service ]]; then
    if ! cp /etc/systemd/system/pocket48-voice-monitor.service \
      "$unit_backup"; then
      rm -f "$unit_backup"
      return 1
    fi
    had_previous_unit=true
  fi
  if ! rm -f \
    "$VOICE_MONITOR_READY_FILE" \
    "$VOICE_MONITOR_WANG_RUIQI_READY_FILE"; then
    rm -f "$unit_backup"
    return 1
  fi
  if [[ "$had_previous_unit" == true ]]; then
    if ! systemctl stop pocket48-voice-monitor.service \
      || systemctl is-active --quiet \
        pocket48-voice-monitor.service; then
      rm -f "$unit_backup"
      return 1
    fi
  fi
  if ! install -m 0644 "$candidate_unit" \
    /etc/systemd/system/pocket48-voice-monitor.service \
    || ! systemctl daemon-reload; then
    restore_voice_monitor_unit "$unit_backup" "$had_previous_unit" || true
    if [[ "$had_previous" == true ]]; then
      systemctl start pocket48-voice-monitor.service || true
    fi
    rm -f "$unit_backup"
    return 1
  fi
  if ! atomic_symlink "$release_dir" "$VOICE_MONITOR_LINK"; then
    restore_voice_monitor_unit "$unit_backup" "$had_previous_unit" || true
    systemctl start pocket48-voice-monitor.service || true
    rm -f "$unit_backup"
    return 1
  fi
  if ! systemctl start pocket48-voice-monitor.service; then
    restore_voice_monitor_unit "$unit_backup" "$had_previous_unit" || true
    if [[ "$had_previous" == true ]] \
      && atomic_symlink "$previous" "$VOICE_MONITOR_LINK"; then
      systemctl start pocket48-voice-monitor.service || true
    else
      rm -f "$VOICE_MONITOR_LINK"
    fi
    rm -f "$unit_backup"
    return 1
  fi
  for _ in $(seq 1 30); do
    if systemctl is-active --quiet pocket48-voice-monitor.service \
      && voice_monitor_release_ready "$release_dir"; then
      monitor_ready=true
      break
    fi
    sleep 1
  done
  if [[ "$monitor_ready" == true ]]; then
    for _ in $(seq 1 5); do
      sleep 2
      if ! systemctl is-active --quiet pocket48-voice-monitor.service \
        || ! voice_monitor_release_ready "$release_dir"; then
        monitor_ready=false
        break
      fi
    done
  fi
  if [[ "$monitor_ready" != true ]]; then
    systemctl stop pocket48-voice-monitor.service || true
    restore_voice_monitor_unit "$unit_backup" "$had_previous_unit" || true
    if [[ "$had_previous" == true ]] \
      && atomic_symlink "$previous" "$VOICE_MONITOR_LINK"; then
      systemctl start pocket48-voice-monitor.service || true
    else
      rm -f "$VOICE_MONITOR_LINK"
    fi
    rm -f "$unit_backup"
    return 1
  fi
  rm -f "$unit_backup"
  return 0
}
