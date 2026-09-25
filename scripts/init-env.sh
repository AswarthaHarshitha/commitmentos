#!/usr/bin/env bash
# Create .env from .env.example (if missing) and fill every EMPTY secret with a random value.
# Idempotent: existing values are never overwritten.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -f .env ] || cp .env.example .env

SECRET_KEYS=(POSTGRES_PASSWORD JWT_SECRET N8N_INBOUND_SECRET N8N_OUTBOUND_SECRET N8N_ENCRYPTION_KEY)

rand_hex() { openssl rand -hex 32; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
while IFS= read -r line || [ -n "$line" ]; do
  replaced=0
  for key in "${SECRET_KEYS[@]}"; do
    # "KEY=" possibly followed by whitespace/comment, but no value
    if [[ "$line" =~ ^${key}=[[:space:]]*(#.*)?$ ]]; then
      comment=""
      [[ "$line" =~ (#.*)$ ]] && comment="  ${BASH_REMATCH[1]}"
      printf '%s=%s%s\n' "$key" "$(rand_hex)" "$comment" >> "$tmp"
      replaced=1
      break
    fi
  done
  [ "$replaced" -eq 1 ] || printf '%s\n' "$line" >> "$tmp"
done < .env
cat "$tmp" > .env
chmod 600 .env

echo "✔ .env ready (secrets generated where empty). Review it, then: docker compose up -d --build"
