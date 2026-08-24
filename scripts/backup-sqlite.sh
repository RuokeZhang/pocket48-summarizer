#!/usr/bin/env bash

set -euo pipefail

database="/var/lib/pocket48-summarizer/pocket48.sqlite3"
backup_dir="/var/backups/pocket48-summarizer"

if [[ ! -f "$database" ]]; then
  exit 0
fi

mkdir -p "$backup_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$backup_dir/.pocket48-$timestamp.sqlite3.tmp"
destination="$backup_dir/pocket48-$timestamp.sqlite3"

rm -f -- "$temporary"
sqlite3 "$database" ".timeout 10000" ".backup '$temporary'"
chmod 600 "$temporary"
mv "$temporary" "$destination"

find "$backup_dir" -type f -name 'pocket48-*.sqlite3' -mtime +14 \
  -exec rm -f -- {} +
