# Doco Backend Workspace

Doco is a Python 3.12 uv workspace containing two independently deployable APIs and one versioned Contracts package.

```text
services/chat/       frontend-facing chat, threads, memories, and document compatibility proxy
services/document/   invoice extraction, persistence, gRPC transport, and PDF generation
packages/contracts/  documents.v1 protobuf source and generated Python bindings
infra/               llama.cpp deployment and the existing Alloy/Loki/Grafana stack
```

Chat and Document own separate SQLite databases. They share neither application code nor storage. Chat calls Document through `documents.v1.DocumentService` and preserves the frontend's existing HTTP paths through a compatibility proxy. Both services may use the shared llama.cpp endpoint.

## Local startup

Docker Desktop/Engine with Compose and `curl` or `wget` are required.

```bash
./start.sh
```

The script works in Git Bash, macOS, and Linux. It combines `compose.yaml` with `compose.dev.yaml`, automatically adds `compose.gpu.yaml` when Docker can access an NVIDIA GPU, downloads Qwen when absent, runs both migration jobs, and starts the stack. Override detection with `LLAMA_ACCELERATOR=cpu` or `LLAMA_ACCELERATOR=cuda`.

| Endpoint | Address |
|---|---|
| Chat HTTP / frontend API | `http://localhost:8000` |
| Document HTTP | `http://localhost:8001` |
| Document gRPC | `127.0.0.1:50051` |
| llama.cpp | `http://127.0.0.1:8080` |
| Grafana | `http://localhost:3000` |

The CUDA image is compiled only for RTX 3090 compute capability 8.6. A CPU-only Ubuntu VPS uses the base and development files without the GPU override.

## Development

Install uv 0.11.32 and run from this directory:

```bash
uv sync --frozen
uv run --frozen --package doco-chat-service pytest services/chat/tests -q
uv run --frozen --package doco-document-service pytest services/document/tests -q
uv run --frozen --package doco-backend-contracts pytest packages/contracts/tests -q
```

Regenerate protobuf bindings with `./scripts/generate-document-contract.sh`. Apply migrations with:

```bash
uv run --package doco-chat-service alembic -c services/chat/alembic.ini upgrade head
uv run --package doco-document-service alembic -c services/document/alembic.ini upgrade head
```

Runtime state is ignored under `services/chat/data`, `services/document/data`, `services/document/generated`, and root `models`.

See [architecture](docs/architecture/overview.md), the [local-startup runbook](docs/runbooks/local-startup.md), and each service README for details.
