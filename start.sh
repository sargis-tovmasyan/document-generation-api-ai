#!/usr/bin/env bash

set -e

FORCE_REBUILD=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print_usage() {
    echo "Usage: ./start.sh [--no-cache|rebuild|--rebuild]"
    echo ""
    echo "Options:"
    echo "  --no-cache, rebuild, --rebuild   Rebuild the API image without cache"
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

echo "Starting Document Generation API..."

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
echo "API:      http://localhost:8000"
echo "API docs: http://localhost:8000/docs"
echo ""
echo "View logs: ${COMPOSE_CMD[*]} logs -f api"
echo "Stop API:  ${COMPOSE_CMD[*]} down"
