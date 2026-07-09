#!/usr/bin/env bash

set -e

FORCE_REBUILD=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "${SCRIPT_DIR}/.env"
    set +a
fi

DEFAULT_MODEL_FILE="MiniCPM5-1B-Q4_K_M.gguf"
DEFAULT_MODEL_URL="https://huggingface.co/openbmb/MiniCPM5-1B-GGUF/resolve/main/MiniCPM5-1B-Q4_K_M.gguf"
MODEL_NAME="${LLAMA_MODEL_FILE:-${LLAMA_MODEL_PATH:-${DEFAULT_MODEL_FILE}}}"
MODEL_NAME="${MODEL_NAME##*/}"
MODEL_DIR="${LLAMA_MODEL_DIR:-${SCRIPT_DIR}/models}"
MODEL_PATH="${MODEL_DIR}/${MODEL_NAME}"
MODEL_URL="${LLAMA_MODEL_URL:-${DEFAULT_MODEL_URL}}"
export LLAMA_MODEL_PATH="${LLAMA_MODEL_PATH:-/models/${MODEL_NAME}}"

print_usage() {
    echo "Usage: ./start.sh [--no-cache|rebuild|--rebuild]"
    echo ""
    echo "Options:"
    echo "  --no-cache, rebuild, --rebuild   Rebuild all local images without cache"
    echo ""
    echo "Model environment variables:"
    echo "  LLAMA_MODEL_FILE                 Model file name inside ./models"
    echo "  LLAMA_MODEL_URL                  Download URL used when model file is missing"
    echo "  LLAMA_MODEL_PATH                 Container model path, default: /models/\${LLAMA_MODEL_FILE}"
    echo "  LLAMA_THREADS                    llama.cpp CPU threads, default: 1"
    echo "  LLAMA_CONTEXT_SIZE               llama.cpp context size, default: 2048"
    echo "  LLAMA_PARALLEL                   llama.cpp parallel requests, default: 1"
}

for arg in "$@"; do
    case "${arg}" in
        --no-cache|rebuild|--rebuild)
            FORCE_REBUILD=true
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "Unknown argument: ${arg}"
            print_usage
            exit 1
            ;;
    esac
done

cd "${SCRIPT_DIR}"

if ! command -v docker > /dev/null 2>&1 || ! docker info > /dev/null 2>&1; then
    echo "Docker is not installed or is not running."
    exit 1
fi

if docker compose version > /dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
elif command -v docker-compose > /dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
else
    echo "Docker Compose is not available."
    exit 1
fi

mkdir -p "${MODEL_DIR}"

if [ ! -f "${MODEL_PATH}" ]; then
    echo "Model file is missing: ${MODEL_PATH}"
    echo "Downloading model: ${MODEL_URL}"
    echo "This is a large download and may take several minutes."

    if command -v curl > /dev/null 2>&1; then
        curl --fail --location --continue-at - --output "${MODEL_PATH}" "${MODEL_URL}"
    elif command -v wget > /dev/null 2>&1; then
        wget --continue --output-document="${MODEL_PATH}" "${MODEL_URL}"
    else
        echo "Neither curl nor wget is installed. Install one of them or manually download:"
        echo "  ${MODEL_URL}"
        echo "to:"
        echo "  ${MODEL_PATH}"
        exit 1
    fi
else
    echo "Model file already exists: ${MODEL_PATH}"
fi

echo "Starting Document Generation API with llama.cpp and observability stack..."

"${COMPOSE_CMD[@]}" down

if [ "${FORCE_REBUILD}" = "true" ]; then
    "${COMPOSE_CMD[@]}" build --no-cache
else
    "${COMPOSE_CMD[@]}" build
fi

"${COMPOSE_CMD[@]}" up -d

echo ""
"${COMPOSE_CMD[@]}" ps
echo ""
echo "Document Generation API started."
echo "API:         http://localhost:8000"
echo "API docs:    http://localhost:8000/docs"
echo "llama.cpp:   http://127.0.0.1:${LLAMA_SERVER_PORT:-8080} (bound to localhost only)"
echo "Model file:  ${MODEL_PATH}"
echo "Model path:  ${LLAMA_MODEL_PATH}"
echo "Grafana:     http://localhost:3000"
echo "Grafana user: ${GRAFANA_ADMIN_USER:-admin}"
echo "Grafana pass: ${GRAFANA_ADMIN_PASSWORD:-admin}"
echo "Loki:        http://localhost:3100"
echo "Alloy UI:    http://localhost:12345"
echo "OTLP HTTP:   http://localhost:4318"
echo ""
echo "llama.cpp settings:"
echo "  LLAMA_THREADS=${LLAMA_THREADS:-1}"
echo "  LLAMA_CONTEXT_SIZE=${LLAMA_CONTEXT_SIZE:-2048}"
echo "  LLAMA_PARALLEL=${LLAMA_PARALLEL:-1}"
echo "  LLAMA_MAX_TOKENS=${LLAMA_MAX_TOKENS:-256}"
echo ""
echo "Application logging flags:"
echo "  APP_LOG_FRONTEND_MESSAGES=${APP_LOG_FRONTEND_MESSAGES:-true}"
echo "  APP_LOG_RESPONSE_BODY=${APP_LOG_RESPONSE_BODY:-true}"
echo "  APP_LOG_LLM_RAW=${APP_LOG_LLM_RAW:-false}"
echo "  APP_LOG_DEBUG_PAYLOADS=${APP_LOG_DEBUG_PAYLOADS:-false}"
echo "  APP_LOG_MAX_FIELD_LENGTH=${APP_LOG_MAX_FIELD_LENGTH:-2000}"
echo ""
echo "Grafana logs:"
echo "  1. Open http://localhost:3000"
echo "  2. Login with ${GRAFANA_ADMIN_USER:-admin}/${GRAFANA_ADMIN_PASSWORD:-admin} unless changed"
echo "  3. Go to Explore"
echo "  4. Select Loki datasource"
echo "  5. Query: {service_name=\"document-generation-api\"} | json"
echo ""
echo "Useful LogQL queries:"
echo "  {service_name=\"document-generation-api\"} | json"
echo "  {service_name=\"document-generation-api\"} | json | event=\"ai.chat.received\""
echo "  {service_name=\"document-generation-api\"} | json | event=\"ai.chat.response.sent\""
echo "  {service_name=\"document-generation-api\"} | json | event=\"invoice.extract.response.sent\""
echo "  {service_name=\"document-generation-api\"} | json | event=\"invoice.pdf.generated\""
echo "  {service_name=\"document-generation-api\"} | json | event=\"llm.request.failed\""
echo "  count_over_time({service_name=\"document-generation-api\"} | json | event=\"invoice.service.create.completed\" [5m])"
echo ""
echo "CLI logs: ${COMPOSE_CMD[*]} logs -f api"
echo "LLM logs: ${COMPOSE_CMD[*]} logs -f llama-server"
echo "Stop all:  ${COMPOSE_CMD[*]} down"
