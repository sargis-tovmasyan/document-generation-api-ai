# Doco Backend

The backend is two independently deployable FastAPI services connected by a versioned gRPC contract.

```text
chat-service/       Chat, threads, streaming, memories, and orchestration
document-service/   Invoice extraction, validation, persistence, PDF generation
contracts/          documents.v1 protobuf contract and generated Python bindings
```

The frontend configures only Chat Service. Chat owns `/ai/chat`, thread, and memory routes, and proxies the existing invoice/download HTTP paths to Document Service for compatibility. Chat's application-level document operations use gRPC; the services do not import each other's code or share a database.

## Start locally

Requirements:

- Docker Desktop on Windows/macOS, or Docker Engine with Compose on Linux
- `curl` or `wget` if the configured model is not present
- NVIDIA Container Toolkit only when using an NVIDIA GPU

From this directory:

```bash
./start.sh
```

The script works in Git Bash on Windows, macOS, and Linux. It automatically uses the RTX 3090 CUDA image when Docker can access the NVIDIA GPU; otherwise it uses the CPU image. Override detection with:

```bash
LLAMA_ACCELERATOR=cpu ./start.sh
LLAMA_ACCELERATOR=cuda ./start.sh
```

Use `./start.sh --no-cache` for a clean image rebuild.

Services:

| Service | Address | Purpose |
|---|---|---|
| Chat HTTP | `http://localhost:8000` | Frontend entry point and chat API |
| Document HTTP | `http://localhost:8001` | Direct document API |
| Document gRPC | `127.0.0.1:50051` | Typed Chat-to-Document boundary |
| llama.cpp | `http://127.0.0.1:8080` | Local OpenAI-compatible model server |
| Grafana | `http://localhost:3000` | Logs and observability |

Docker Desktop shows `doco-backend-chat-service-1` and `doco-backend-document-service-1` as separate containers.

## Service projects

Each service owns its application, tests, dependencies, and image:

```text
chat-service/
  app/
  tests/
  Dockerfile
  requirements.txt
  requirements-dev.txt

document-service/
  app/
  tests/
  templates/
  Dockerfile
  requirements.txt
  requirements-dev.txt
```

Document Service alone contains WeasyPrint/templates and mounts generated files. Chat Service does not contain or import document persistence/rendering code.

## APIs

Chat Service owns:

- `POST /ai/chat`
- `POST /ai/chat/stream`
- `/chat-threads`
- `/shared-memories`
- `/skill-memories`
- compatibility proxy paths `/ai/invoice/*`, `/invoices/*`, and `/generated/*`

Document Service owns:

- `POST /ai/invoice/extract`
- `POST /ai/invoice/generate`
- `POST /invoices/draft/complete`
- `POST /invoices`
- `GET /invoices`
- `DELETE /invoices`
- `GET /invoices/{id}/download`
- `documents.v1.DocumentService` on gRPC port 50051

Both services expose `/health` and `/ready`.

## Data and migration

Service data is separated:

```text
data/chat/chat.db
data/documents/documents.db
generated/
```

When `data/app.db` exists, `start.sh` runs an idempotent migration before startup. It copies chat-owned and document-owned tables into their respective databases, verifies row counts, and records migration markers. The legacy `app.db` is never modified or deleted.

Manual migration:

```bash
python scripts/migrate-monolith-db.py \
  --legacy data/app.db \
  --chat data/chat/chat.db \
  --documents data/documents/documents.db
```

## Model and environment

The default model is Qwen 2.5 3B Instruct Q4_K_M:

```text
Qwen2.5-3B-Instruct-Q4_K_M.gguf
```

Copy `.env.example` to `.env` to override settings. Important variables include:

- `LLAMA_MODEL_FILE`, `LLAMA_MODEL_URL`
- `LLAMA_ACCELERATOR=auto|cuda|cpu`
- `LLAMA_CONTEXT_SIZE`, `LLAMA_THREADS`, `LLAMA_PARALLEL`
- `LLM_CHAT_MAX_TOKENS`, `LLM_TIMEOUT_SECONDS`
- `CHAT_SERVICE_PORT`, `DOCUMENT_SERVICE_PORT`, `DOCUMENT_GRPC_PORT`
- `DOCUMENT_GRPC_TIMEOUT_SECONDS`

The CUDA build targets compute capability 8.6 specifically for the RTX 3090. CPU-only Ubuntu deployments use the base Compose file without CUDA requirements.

## Tests

Use Python 3.12.

```bash
python -m pip install -r chat-service/requirements-dev.txt
PYTHONPATH=chat-service:contracts/python python -m pytest chat-service/tests -q

python -m pip install -r document-service/requirements-dev.txt
PYTHONPATH=document-service:contracts/python python -m pytest document-service/tests -q

python -m pytest tests/contracts tests/architecture tests/integration tests/migration -q
bash tests/start-accelerator-selection.sh
```

Regenerate protobuf bindings after changing the contract:

```bash
./scripts/generate-document-contract.sh
```

CI runs contract, Chat, Document, integration, and Compose checks independently.

## Logs and operations

```bash
docker compose logs -f chat-service
docker compose logs -f document-service
docker compose logs -f llama-server
docker compose ps
docker compose down
```

Useful Loki queries:

```logql
{service_name=~"chat-service|document-service"} | json
{service_name="chat-service"} | json | event="ai.chat.received"
{service_name="document-service"} | json | event="invoice.extract.response.sent"
```

The two services can be deployed separately by building their Dockerfiles from the repository root. Both may point to the same llama.cpp deployment or to different compatible LLM endpoints.
