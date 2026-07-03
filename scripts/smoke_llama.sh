#!/usr/bin/env bash

set -euo pipefail

LLAMA_URL="${LLAMA_URL:-http://127.0.0.1:8080}"

curl --fail --silent --show-error \
  "${LLAMA_URL}/completion" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "User: Say hello in one short sentence.\nAssistant:",
    "n_predict": 32,
    "temperature": 0.2,
    "stop": ["User:"]
  }'

echo ""
