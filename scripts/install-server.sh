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
  fontconfig \
  fonts-lxgw-wenkai \
  fonts-noto-cjk \
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

if ! id pocket48 >/dev/null 2>&1; then
  useradd \
    --system \
    --home-dir /var/lib/pocket48-summarizer \
    --shell /usr/sbin/nologin \
    pocket48
fi

install -d -m 0750 -o pocket48 -g pocket48 \
  /var/lib/pocket48-summarizer \
  /var/lib/pocket48-summarizer/deploy \
  /var/backups/pocket48-summarizer
install -d -m 0750 -o root -g pocket48 /etc/pocket48-summarizer
install -d -m 0755 \
  /opt/pocket48-summarizer-releases \
  /opt/pocket48-summarizer-slots \
  /usr/local/lib/pocket48-summarizer

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
if [[ ! -f /etc/pocket48-summarizer/web-blue.env ]]; then
  printf 'APP_PORT=8000\n' > /etc/pocket48-summarizer/web-blue.env
  chown root:pocket48 /etc/pocket48-summarizer/web-blue.env
  chmod 0640 /etc/pocket48-summarizer/web-blue.env
fi
if [[ ! -f /etc/pocket48-summarizer/web-green.env ]]; then
  printf 'APP_PORT=8001\n' > /etc/pocket48-summarizer/web-green.env
  chown root:pocket48 /etc/pocket48-summarizer/web-green.env
  chmod 0640 /etc/pocket48-summarizer/web-green.env
fi

install -m 0644 "$install_dir/deploy/Caddyfile" /etc/caddy/Caddyfile
if [[ ! -f /etc/caddy/pocket48-upstream.caddy ]]; then
  install -m 0644 "$install_dir/deploy/caddy-upstream.caddy" \
    /etc/caddy/pocket48-upstream.caddy
fi
install -m 0644 "$install_dir/deploy/systemd/pocket48-summarizer.service" \
  /etc/systemd/system/pocket48-summarizer.service
install -m 0644 "$install_dir/deploy/systemd/pocket48-web@.service" \
  /etc/systemd/system/pocket48-web@.service
install -m 0644 "$install_dir/deploy/systemd/pocket48-worker.service" \
  /etc/systemd/system/pocket48-worker.service
install -m 0644 \
  "$install_dir/deploy/systemd/pocket48-summarizer-backup.service" \
  /etc/systemd/system/pocket48-summarizer-backup.service
install -m 0644 \
  "$install_dir/deploy/systemd/pocket48-summarizer-backup.timer" \
  /etc/systemd/system/pocket48-summarizer-backup.timer
install -m 0755 "$install_dir/scripts/backup-sqlite.sh" \
  /usr/local/lib/pocket48-summarizer/backup-sqlite.sh

systemctl daemon-reload
systemctl disable pocket48-summarizer.service || true
systemctl enable pocket48-summarizer-backup.timer
systemctl enable caddy.service

echo
echo "Installation complete. No Pocket48 release was started."
echo "1. Edit /etc/pocket48-summarizer/app.env."
echo "2. Restore the existing SQLite database if needed."
echo "3. Point DNS at this server."
echo "4. Start the backup timer and Caddy."
echo "5. Run scripts/deploy-release.sh HEAD to create the first release."
echo "6. Create the first admin account from the active slot."
