#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
repository="${GITHUB_REPOSITORY:-RuokeZhang/pocket48-summarizer}"
repository_owner="${repository%%/*}"
production_host="${PRODUCTION_HOST:-47.76.255.186}"
production_user="${PRODUCTION_SSH_USER:-root}"
admin_key="${PRODUCTION_ADMIN_KEY:-$HOME/.ssh/p48-ecs.pem}"
deploy_key="${PRODUCTION_DEPLOY_KEY_PATH:-$HOME/.ssh/p48-github-actions}"
remote_command="/usr/local/sbin/pocket48-github-deploy"
command_file="$script_dir/github-actions-deploy-command.sh"

for command in gh ssh ssh-keygen ssh-keyscan base64; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done
test -f "$admin_key"
test -f "$command_file"
gh auth status --hostname github.com >/dev/null
active_login="$(gh api user --jq .login)"
if [[ "$active_login" != "$repository_owner" ]]; then
  echo "Switch GitHub CLI to $repository_owner before bootstrapping:" >&2
  echo "  gh auth switch --hostname github.com --user $repository_owner" >&2
  exit 1
fi

if [[ ! -f "$deploy_key" ]]; then
  umask 077
  ssh-keygen \
    -q \
    -t ed25519 \
    -N "" \
    -C "pocket48-github-actions" \
    -f "$deploy_key"
fi
test -f "$deploy_key.pub"

public_key_base64="$(base64 < "$deploy_key.pub" | tr -d '\n')"
command_base64="$(base64 < "$command_file" | tr -d '\n')"

ssh \
  -i "$admin_key" \
  -o BatchMode=yes \
  -o ConnectTimeout=15 \
  -o StrictHostKeyChecking=accept-new \
  "$production_user@$production_host" \
  "bash -s -- '$public_key_base64' '$command_base64' '$remote_command'" \
  <<'REMOTE'
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "The production bootstrap must run as root." >&2
  exit 1
fi

public_key="$(printf '%s' "$1" | base64 -d)"
command_body="$(printf '%s' "$2" | base64 -d)"
remote_command="$3"
key_material="$(awk '{print $2}' <<< "$public_key")"
root_home="$(getent passwd root | cut -d: -f6)"
authorized_keys="$root_home/.ssh/authorized_keys"
temporary_keys="$(mktemp)"
temporary_command="$(mktemp)"
trap 'rm -f "$temporary_keys" "$temporary_command"' EXIT

printf '%s' "$command_body" > "$temporary_command"
install -m 0755 -o root -g root "$temporary_command" "$remote_command"

install -d -m 0700 -o root -g root "$root_home/.ssh"
if [[ -f "$authorized_keys" ]]; then
  awk -v key="$key_material" 'index($0, key) == 0' \
    "$authorized_keys" > "$temporary_keys"
fi
printf 'restrict,command="%s" %s\n' "$remote_command" "$public_key" \
  >> "$temporary_keys"
install -m 0600 -o root -g root "$temporary_keys" "$authorized_keys"

installed_packages="$(
  dpkg-query -W -f='${Status}\n' \
    fontconfig fonts-lxgw-wenkai fonts-noto-cjk 2>/dev/null \
    | grep -c '^install ok installed$' \
    || true
)"
if [[ "$installed_packages" != "3" ]]; then
  apt-get update
  apt-get install -y fontconfig fonts-lxgw-wenkai fonts-noto-cjk
fi
command -v ffprobe >/dev/null
ffmpeg -nostdin -hide_banner -filters 2>/dev/null \
  | awk '$2 == "ass" { found = 1 } END { exit !found }'
fc-match --format='%{family}\n' "Noto Sans CJK SC" \
  | head -n 1 \
  | grep -F "Noto Sans CJK SC"
fc-match --format='%{family}\n' "Noto Serif CJK SC" \
  | head -n 1 \
  | grep -F "Noto Serif CJK SC"
fc-match --format='%{family}\n' "LXGW WenKai" \
  | head -n 1 \
  | grep -F "LXGW WenKai"
REMOTE

known_hosts="$(
  ssh-keygen -F "$production_host" -f "$HOME/.ssh/known_hosts" 2>/dev/null \
    | grep -v '^#' \
    || true
)"
if [[ -z "$known_hosts" ]]; then
  known_hosts="$(ssh-keyscan -H "$production_host" 2>/dev/null)"
fi
test -n "$known_hosts"

gh variable set PRODUCTION_HOST \
  --repo "$repository" \
  --body "$production_host"
gh variable set PRODUCTION_SSH_USER \
  --repo "$repository" \
  --body "$production_user"
gh api \
  --method PUT \
  "repos/$repository/environments/production" \
  >/dev/null
gh secret set PRODUCTION_DEPLOY_KEY \
  --repo "$repository" \
  --env production \
  < "$deploy_key"
printf '%s\n' "$known_hosts" \
  | gh secret set PRODUCTION_SSH_KNOWN_HOSTS \
      --repo "$repository" \
      --env production

echo "GitHub Actions production deployment is configured."
if [[ "${SKIP_INITIAL_DEPLOY:-false}" == "true" ]]; then
  echo "Initial deployment was skipped."
  echo "Run it with:"
  echo "  gh workflow run deploy-production.yml --repo $repository --ref main"
  exit 0
fi

run_output="$(
  gh workflow run deploy-production.yml \
    --repo "$repository" \
    --ref main
)"
printf '%s\n' "$run_output"
run_id="$(sed -nE 's#^.*/actions/runs/([0-9]+).*$#\1#p' <<< "$run_output")"
if [[ -n "$run_id" ]]; then
  gh run watch "$run_id" \
    --repo "$repository" \
    --compact \
    --exit-status
else
  echo "Deployment was triggered. Track it in GitHub Actions:"
  echo "  https://github.com/$repository/actions/workflows/deploy-production.yml"
fi
