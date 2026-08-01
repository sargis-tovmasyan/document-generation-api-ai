# Service Outage Diagnosis

Start with `docker compose -f compose.yaml -f compose.dev.yaml ps` and logs for the failing service and its migration job. Check `/health`, then `/ready`. A failed readiness check usually indicates database access; a missing service container after startup usually indicates a failed one-shot migration.

For Document outages, verify ports 8001/50051 and `DOCUMENT_GRPC_TARGET`; ordinary Chat should remain healthy while document intents return 503. For both services failing AI requests, inspect llama health/model logs. For missing telemetry, inspect Alloy and Loki before Grafana.
