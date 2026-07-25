#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NVIDIA_CUDA_VERSION="${NVIDIA_CUDA_VERSION:-12.6.3}"
FORCE_REBUILD=false
COMPOSE_CMD=()
COMPOSE_FILES=()

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
    echo "  LLAMA_CONTEXT_SIZE               llama.cpp context size, default: 4096"
    echo "  LLAMA_PARALLEL                   llama.cpp parallel requests, default: 1"
    echo "  LLAMA_ACCELERATOR                auto (default), cuda, or cpu"
    echo "  NVIDIA_CUDA_VERSION              CUDA image version, default: 12.6.3"
}

validate_accelerator() {
    case "$1" in
        auto|cuda|cpu)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

docker_has_nvidia_runtime() {
    docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'
}

probe_nvidia_gpu() {
    docker run --rm --gpus all \
        "nvidia/cuda:${NVIDIA_CUDA_VERSION}-base-ubuntu24.04" \
        nvidia-smi > /dev/null 2>&1
}

select_accelerator() {
    local requested="$1"

    if [ "${requested}" = "cpu" ]; then
        printf '%s\n' cpu
        return 0
    fi

    if docker_has_nvidia_runtime && probe_nvidia_gpu; then
        printf '%s\n' cuda
        return 0
    fi

    if [ "${requested}" = "cuda" ]; then
        echo "CUDA was requested, but Docker cannot access an NVIDIA GPU." >&2
        return 1
    fi

    echo "Docker NVIDIA GPU access is unavailable; using CPU mode." >&2
    printf '%s\n' cpu
}

configure_compose_files() {
    COMPOSE_FILES=(-f docker-compose.yml)
    if [ "$1" = "cuda" ]; then
        COMPOSE_FILES+=(-f docker-compose.gpu.yml)
    fi
}

run_compose() {
    "${COMPOSE_CMD[@]}" "${COMPOSE_FILES[@]}" "$@"
}

detect_compose() {
    if docker compose version > /dev/null 2>&1; then
        COMPOSE_CMD=(docker compose)
    elif command -v docker-compose > /dev/null 2>&1; then
        COMPOSE_CMD=(docker-compose)
    else
        echo "Docker Compose is not available."
        return 1
    fi
}

main() {
    local arg
    local requested_accelerator
    local default_model_file
    local default_model_url
    local model_name
    local model_dir
    local model_path
    local model_url

    if [ -f "${SCRIPT_DIR}/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        . "${SCRIPT_DIR}/.env"
        set +a
    fi

    for arg in "$@"; do
        case "${arg}" in
            --no-cache|rebuild|--rebuild)
                FORCE_REBUILD=true
                ;;
            -h|--help)
                print_usage
                return 0
                ;;
            *)
                echo "Unknown argument: ${arg}"
                print_usage
                return 1
                ;;
        esac
    done

    requested_accelerator="${LLAMA_ACCELERATOR:-auto}"
    if ! validate_accelerator "${requested_accelerator}"; then
        echo "Invalid LLAMA_ACCELERATOR: ${requested_accelerator}" >&2
        echo "Expected one of: auto, cuda, cpu" >&2
        return 1
    fi

    cd "${SCRIPT_DIR}"

    if ! command -v docker > /dev/null 2>&1 || ! docker info > /dev/null 2>&1; then
        echo "Docker is not installed or is not running."
        return 1
    fi

    detect_compose

    ACTIVE_ACCELERATOR="$(select_accelerator "${requested_accelerator}")" || return 1
    export ACTIVE_ACCELERATOR
    configure_compose_files "${ACTIVE_ACCELERATOR}"
    echo "Selected llama.cpp accelerator: ${ACTIVE_ACCELERATOR}"

    default_model_file="Qwen2.5-3B-Instruct-Q4_K_M.gguf"
    default_model_url="https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
    model_name="${LLAMA_MODEL_FILE:-${LLAMA_MODEL_PATH:-${default_model_file}}}"
    model_name="${model_name##*/}"
    model_dir="${LLAMA_MODEL_DIR:-${SCRIPT_DIR}/models}"
    model_path="${model_dir}/${model_name}"
    model_url="${LLAMA_MODEL_URL:-${default_model_url}}"
    export LLAMA_MODEL_PATH="${LLAMA_MODEL_PATH:-/models/${model_name}}"
    export MSYS2_ENV_CONV_EXCL="${MSYS2_ENV_CONV_EXCL:+${MSYS2_ENV_CONV_EXCL};}LLAMA_MODEL_PATH"

    mkdir -p "${model_dir}"

    if [ ! -f "${model_path}" ]; then
        echo "Model file is missing: ${model_path}"
        echo "Downloading model: ${model_url}"
        echo "This is a large download and may take several minutes."

        if command -v curl > /dev/null 2>&1; then
            curl --fail --location --continue-at - --output "${model_path}" "${model_url}"
        elif command -v wget > /dev/null 2>&1; then
            wget --continue --output-document="${model_path}" "${model_url}"
        else
            echo "Neither curl nor wget is installed. Install one of them or manually download:"
            echo "  ${model_url}"
            echo "to:"
            echo "  ${model_path}"
            return 1
        fi
    else
        echo "Model file already exists: ${model_path}"
    fi

    echo "Starting Document Generation API with llama.cpp and observability stack..."

    run_compose down

    if [ "${FORCE_REBUILD}" = "true" ]; then
        run_compose build --no-cache
    else
        run_compose build
    fi

    run_compose up -d

    echo ""
    run_compose ps
    echo ""
    echo "Document Generation API started."
    echo "API:         http://localhost:8000"
    echo "API docs:    http://localhost:8000/docs"
    echo "llama.cpp:   http://127.0.0.1:${LLAMA_SERVER_PORT:-8080} (bound to localhost only)"
    echo "Model file:  ${model_path}"
    echo "Model path:  ${LLAMA_MODEL_PATH}"
    echo "Grafana:     http://localhost:3000"
    echo "Grafana user: ${GRAFANA_ADMIN_USER:-admin}"
    echo "Grafana pass: ${GRAFANA_ADMIN_PASSWORD:-admin}"
    echo "Loki:        http://localhost:3100"
    echo "Alloy UI:    http://localhost:12345"
    echo "OTLP HTTP:   http://localhost:4318"
    echo ""
    echo "llama.cpp settings:"
    echo "  LLAMA_ACCELERATOR=${LLAMA_ACCELERATOR:-auto}"
    echo "  ACTIVE_ACCELERATOR=${ACTIVE_ACCELERATOR}"
    echo "  LLAMA_THREADS=${LLAMA_THREADS:-1}"
    echo "  LLAMA_CONTEXT_SIZE=${LLAMA_CONTEXT_SIZE:-4096}"
    echo "  LLAMA_PARALLEL=${LLAMA_PARALLEL:-1}"
    echo "  LLAMA_MAX_TOKENS=${LLAMA_MAX_TOKENS:-1024}"
    echo "  LLM_CHAT_MAX_TOKENS=${LLM_CHAT_MAX_TOKENS:-1024}"
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
    echo "CLI logs: ${COMPOSE_CMD[*]} ${COMPOSE_FILES[*]} logs -f api"
    echo "LLM logs: ${COMPOSE_CMD[*]} ${COMPOSE_FILES[*]} logs -f llama-server"
    echo "Stop all:  ${COMPOSE_CMD[*]} ${COMPOSE_FILES[*]} down"
}

if [ "${START_SH_SOURCE_ONLY:-false}" != "true" ]; then
    main "$@"
fi
