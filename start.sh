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

echo "Starting Document Generation API with observability stack..."

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
echo "Grafana:     http://localhost:3000"
echo "Grafana user: admin"
echo "Grafana pass: admin"
echo "Loki:        http://localhost:3100"
echo "Alloy UI:    http://localhost:12345"
echo "OTLP HTTP:   http://localhost:4318"
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
echo "  2. Login with admin/admin unless changed with GRAFANA_ADMIN_USER/GRAFANA_ADMIN_PASSWORD"
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
echo "Stop all:  ${COMPOSE_CMD[*]} down"
