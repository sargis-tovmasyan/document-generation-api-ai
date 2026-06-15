# Document Generation API

Lightweight FastAPI MVP for generating invoice PDFs and calling a local
`llama.cpp` server.

## Current Features

- Validate invoice requests with Pydantic.
- Calculate item amounts, subtotal, and total in the backend.
- Store invoices and items in SQLite.
- Render a trusted Jinja2 invoice template.
- Generate and store PDFs with WeasyPrint.
- List and download generated invoices.
- Send a test prompt to `llama-server`.
- Limit LLM processing to one request at a time.
- Persist SQLite and generated files through Docker volumes.

Not implemented yet:

- Converting chat messages into structured invoice data.
- AI-generated or user-editable templates.
- Authentication and rate limiting.
- Taxes, discounts, queues, and additional document types.

## Architecture

```text
Client
  |
  +--> FastAPI --> Pydantic --> SQLite
  |                    |
  |                    +--> Jinja2 --> WeasyPrint --> PDF storage
  |
  +--> FastAPI --> LlmClient --> llama-server /completion
```

The LLM only generates text. Invoice validation, calculations, storage, and PDF
generation remain controlled by the backend.

## Project Structure

```text
app/
  main.py
  config.py
  database.py
  schemas.py
  routes/
    ai.py
    invoices.py
  services/
    invoice_service.py
    llm_client.py
    pdf_service.py
  tests/
templates/
  invoice.html
data/
generated/invoices/
Dockerfile
docker-compose.yml
start.sh
```

## Environment

Create local settings from the example:

```bash
cp .env.example .env
```

Available variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://127.0.0.1:8080` | Reachable llama-server address |
| `LLM_COMPLETION_ENDPOINT` | `/completion` | llama.cpp completion route |
| `LLM_TIMEOUT_SECONDS` | `120` | Request timeout |
| `LLM_MAX_TOKENS` | `80` | Maximum generated tokens |
| `LLM_TEMPERATURE` | `0.4` | Generation temperature |

`.env` is ignored by Git.

## Start the API

Docker must be installed and running.

```bash
./start.sh
```

Force a build without Docker cache:

```bash
./start.sh --no-cache
```

Equivalent manual command:

```bash
docker compose up --build -d
```

Local addresses:

```text
API:      http://localhost:8000
API docs: http://localhost:8000/docs
```

Useful commands:

```bash
docker compose logs -f api
docker compose ps
docker compose down
```

## Run llama-server

On the VPS:

```bash
cd ~/llama.cpp

./build/bin/llama-server \
  -m models/SmolLM2-360M-Instruct-Q4_K_M.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  -c 512
```

Verify it from inside the VPS:

```bash
curl http://127.0.0.1:8080/completion \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "User: Say hello in one short sentence.\nAssistant:",
    "n_predict": 40
  }'
```

Keep `llama-server` bound to `127.0.0.1` in production. The public client should
call the FastAPI backend, not port `8080`.

## macOS Development With VPS LLM

Because the VPS llama-server listens only on localhost, use an SSH tunnel:

```bash
ssh -N -L 8080:127.0.0.1:8080 ubuntu@161.153.29.155
```

For an API running inside Docker Desktop, set:

```env
LLM_BASE_URL=http://host.docker.internal:8080
```

Then restart:

```bash
./start.sh
```

Do not use `network_mode: host` for local Docker Desktop testing. The current
Compose file publishes `8000:8000` so `localhost:8000` works on macOS.

## Linux VPS Deployment

When the API container and host-bound llama-server run on the same Linux VPS,
change the API service in `docker-compose.yml` from:

```yaml
ports:
  - "8000:8000"
```

to:

```yaml
network_mode: "host"
```

Set the VPS `.env`:

```env
LLM_BASE_URL=http://127.0.0.1:8080
LLM_COMPLETION_ENDPOINT=/completion
LLM_TIMEOUT_SECONDS=120
LLM_MAX_TOKENS=80
LLM_TEMPERATURE=0.4
```

Then run:

```bash
./start.sh
```

With Linux host networking, Uvicorn listens on VPS port `8000`. Keep Oracle
Cloud and OS firewall rules for port `8080` closed.

## API Endpoints

### Health

```http
GET /health
```

```bash
curl http://localhost:8000/health
```

Response:

```json
{"status":"ok"}
```

### Test AI

```http
POST /ai/test
```

```bash
curl -X POST http://localhost:8000/ai/test \
  -H "Content-Type: application/json" \
  -d '{"message":"Create a short invoice note for website design."}'
```

Response shape:

```json
{"answer":"Thank you for your business."}
```

If llama-server is unreachable, the endpoint returns HTTP `502`.

### Create Invoice

```http
POST /invoices
```

```bash
curl -X POST http://localhost:8000/invoices \
  -H "Content-Type: application/json" \
  -d '{
    "invoice_number": "INV-001",
    "issue_date": "2026-06-15",
    "due_date": "2026-06-22",
    "currency": "USD",
    "business": {
      "name": "Sargis Studio",
      "email": "hello@example.com",
      "address": "Yerevan, Armenia"
    },
    "client": {
      "name": "Alex Johnson",
      "email": "alex@example.com",
      "address": "New York, USA"
    },
    "items": [
      {
        "description": "Website design",
        "quantity": 1,
        "unit_price": 300
      },
      {
        "description": "Hosting setup",
        "quantity": 1,
        "unit_price": 50
      }
    ],
    "notes": "Thank you for your business.",
    "payment_terms": "Payment due within 7 days."
  }'
```

Response shape:

```json
{
  "id": 1,
  "invoice_number": "INV-001",
  "total": 350.0,
  "pdf_url": "/generated/invoices/INV-001-generated-id.pdf"
}
```

The client does not provide totals or file paths.

### List Invoices

```bash
curl http://localhost:8000/invoices
```

### Download Invoice

```bash
curl -o invoice.pdf http://localhost:8000/invoices/1/download
```

The `pdf_url` returned during creation can also be opened directly.

## Storage

- SQLite database: `data/app.db`
- Generated PDFs: `generated/invoices/`
- Trusted template: `templates/invoice.html`

The `data/` and `generated/` directories are mounted into the container, so
their contents survive image rebuilds.

Invoice numbers are unique. Generated filenames are sanitized and include a
random suffix. User input is autoescaped by Jinja2.

## Tests

To run tests directly on the host, first install project dependencies in a
virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s app/tests -v
```

Run them inside the built API container:

```bash
docker compose run --rm api python -m unittest discover -s app/tests -v
```

Tests cover:

- Invoice and AI request validation.
- Currency and text normalization.
- Due-date validation.
- Monetary calculation and rounding.
- Safe PDF filename generation.
- LLM request payload and answer parsing.
- LLM HTTP, invalid JSON, and empty-answer failures.
- AI route success and HTTP `502` mapping.

The unit tests mock external LLM calls and do not contact the VPS.

## Security Notes

- Do not publicly expose llama-server port `8080`.
- Do not allow the LLM to generate executable code or production templates.
- Do not pass secrets in prompts.
- Add authentication and rate limiting before real-user deployment.
- The current `/ai/test` endpoint is intentionally basic and unauthenticated.

## Current LLM Limitation

`SmolLM2-360M-Instruct-Q4_K_M.gguf` is suitable for short text generation and
basic extraction experiments, but it should not be trusted for calculations or
business decisions. Structured invoice extraction is the next planned AI
feature and must be validated by Pydantic before invoice creation.
