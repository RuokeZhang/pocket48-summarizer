#!/usr/bin/env bash

set -euo pipefail

install_dir="/opt/pocket48-summarizer"
root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

if [[ "$root_dir" != "$install_dir" ]]; then
  echo "Clone the repository to $install_dir before running this script." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  debian-archive-keyring \
  debian-keyring \
  ffmpeg \
  git \
  gnupg \
  python3 \
  python3-venv \
  sqlite3

if ! command -v caddy >/dev/null 2>&1; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --batch --yes --dearmor \
      -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi
systemctl stop caddy || true

if ! id pocket48 >/dev/null 2>&1; then
  useradd \
    --system \
    --home-dir /var/lib/pocket48-summarizer \
    --shell /usr/sbin/nologin \
    pocket48
fi

install -d -m 0750 -o pocket48 -g pocket48 \
  /var/lib/pocket48-summarizer \
  /var/backups/pocket48-summarizer
install -d -m 0750 -o root -g pocket48 /etc/pocket48-summarizer

python3 -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/python" -m pip install --upgrade pip
"$install_dir/.venv/bin/python" -m pip install \
  -r "$install_dir/requirements.lock"
"$install_dir/.venv/bin/python" -m pip install \
  --no-deps "$install_dir"

if [[ ! -f /etc/pocket48-summarizer/app.env ]]; then
  install -m 0640 -o root -g pocket48 \
    "$install_dir/deploy/app.env.example" \
    /etc/pocket48-summarizer/app.env
fi

install -m 0644 "$install_dir/deploy/Caddyfile" /etc/caddy/Caddyfile
install -m 0644 "$install_dir/deploy/systemd/pocket48-summarizer.service" \
  /etc/systemd/system/pocket48-summarizer.service
install -m 0644 \
  "$install_dir/deploy/systemd/pocket48-summarizer-backup.service" \
  /etc/systemd/system/pocket48-summarizer-backup.service
install -m 0644 \
  "$install_dir/deploy/systemd/pocket48-summarizer-backup.timer" \
  /etc/systemd/system/pocket48-summarizer-backup.timer

systemctl daemon-reload
systemctl enable pocket48-summarizer.service
systemctl enable pocket48-summarizer-backup.timer
systemctl enable caddy.service

echo
echo "Installation complete. Services were not started."
echo "1. Edit /etc/pocket48-summarizer/app.env."
echo "2. Restore the existing SQLite database if needed."
echo "3. Create the first admin account."
echo "4. Point DNS at this server, then start the app, backup timer, and Caddy."
