# Backend Architecture

The backend consists of independently buildable Chat and Document services plus a versioned Contracts package. The frontend talks only to Chat on port 8000. Chat owns conversations, threads, UI settings, session/shared/skill memories, LLM chat orchestration, and HTTP compatibility proxies. Document owns invoices, invoice items, PDF files, extraction, rendering, HTTP port 8001, and gRPC port 50051.

Each service has its own SQLite database and Alembic history. There are no cross-service database reads. Chat calls `documents.v1.DocumentService`; both services call the shared llama.cpp HTTP endpoint. If Document is unavailable, ordinary chat remains available while document intents return a typed 503.

Alloy receives application logs and traces and forwards logs to Loki; Grafana queries Loki. This refactor does not add Prometheus or an OpenTelemetry Collector.

Deployment composes a production base, local bind/port overrides, and an optional CUDA override compiled for RTX 3090 compute capability 8.6.
