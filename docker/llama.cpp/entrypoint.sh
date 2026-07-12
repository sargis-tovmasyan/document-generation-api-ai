#!/usr/bin/env sh

set -eu

MODEL_PATH="${LLAMA_MODEL_PATH:-/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf}"
HOST="${LLAMA_HOST:-0.0.0.0}"
PORT="${LLAMA_PORT:-8080}"
THREADS="${LLAMA_THREADS:-1}"
CONTEXT_SIZE="${LLAMA_CONTEXT_SIZE:-4096}"
PARALLEL="${LLAMA_PARALLEL:-1}"
MAX_TOKENS="${LLAMA_MAX_TOKENS:-1024}"
EXTRA_ARGS="${LLAMA_EXTRA_ARGS:-}"

if [ ! -f "${MODEL_PATH}" ]; then
    echo "Missing llama.cpp model file: ${MODEL_PATH}" >&2
    echo "Mount the model into /models or run ./start.sh to download the default model." >&2
    exit 1
fi

echo "Starting llama.cpp server"
echo "  model:        ${MODEL_PATH}"
echo "  host:         ${HOST}"
echo "  port:         ${PORT}"
echo "  threads:      ${THREADS}"
echo "  context size: ${CONTEXT_SIZE}"
echo "  parallel:     ${PARALLEL}"
echo "  max tokens:   ${MAX_TOKENS}"

# shellcheck disable=SC2086
exec llama-server \
    -m "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${PORT}" \
    -t "${THREADS}" \
    -c "${CONTEXT_SIZE}" \
    -n "${MAX_TOKENS}" \
    --parallel "${PARALLEL}" \
    ${EXTRA_ARGS}
