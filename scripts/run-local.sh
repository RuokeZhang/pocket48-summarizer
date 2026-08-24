#!/bin/bash

set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
KEYCHAIN_ACCOUNT="${USER:?USER is not set}"

keychain_get() {
  /usr/bin/security find-generic-password \
    -a "$KEYCHAIN_ACCOUNT" \
    -s "$1" \
    -w
}

export ALIYUN_ACCESS_KEY_ID
ALIYUN_ACCESS_KEY_ID="$(keychain_get com.ruoke.secrets.aliyun-oss-access-key-id)"

export ALIYUN_ACCESS_KEY_SECRET
ALIYUN_ACCESS_KEY_SECRET="$(keychain_get com.ruoke.secrets.aliyun-oss-access-key-secret)"

export ALIYUN_OSS_ENDPOINT
ALIYUN_OSS_ENDPOINT="$(keychain_get com.ruoke.aliyun-oss.endpoint)"

export ALIYUN_OSS_BUCKET
ALIYUN_OSS_BUCKET="$(keychain_get com.ruoke.aliyun-oss.bucket)"

export DASHSCOPE_API_KEY
DASHSCOPE_API_KEY="$(keychain_get com.ruoke.secrets.dashscope-api-key)"

export DASHSCOPE_BASE_URL
DASHSCOPE_BASE_URL="$(keychain_get com.ruoke.dashscope.default-workspace.api-host)"
case "$DASHSCOPE_BASE_URL" in
  http://*|https://*) ;;
  *) DASHSCOPE_BASE_URL="https://$DASHSCOPE_BASE_URL" ;;
esac
export DASHSCOPE_ASR_MODEL="fun-asr"

export LLM_BASE_URL
LLM_BASE_URL="$(keychain_get com.ruoke.dashscope.default-workspace.openai-compatible-url)"

export LLM_API_KEY="$DASHSCOPE_API_KEY"
export LLM_MODEL="qwen3.7-plus"
export LLM_TIMEOUT_SECONDS="600"

# Ignore the stale local proxy configured on this machine. All required
# services are reachable directly.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
unset http_proxy https_proxy all_proxy

if [[ ! -x "$ROOT_DIR/.venv/bin/pocket48-summarizer" ]]; then
  echo "Missing local installation. Follow README.md 本地安装 first." >&2
  exit 1
fi

cd "$ROOT_DIR"
exec "$ROOT_DIR/.venv/bin/pocket48-summarizer"
