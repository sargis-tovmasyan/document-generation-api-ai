# Document Generation API

Lightweight FastAPI MVP that validates invoice data, stores invoice metadata in
SQLite, renders a trusted Jinja2 template, and generates a PDF with WeasyPrint.

## Run with Docker Compose

```bash
docker compose up --build
```

The API is available at `http://localhost:8000`. SQLite data and generated PDFs
are persisted in the mounted `data/` and `generated/` directories.

## Endpoints

- `GET /health`
- `POST /ai/test`
- `POST /invoices`
- `GET /invoices`
- `GET /invoices/{invoice_id}/download`
- `GET /generated/invoices/{filename}`

Interactive API documentation is available at `http://localhost:8000/docs`.

## Local LLM

Run `llama-server` on the VPS host:

```bash
cd ~/llama.cpp

./build/bin/llama-server \
  -m models/SmolLM2-360M-Instruct-Q4_K_M.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  -c 512
```

The Compose service publishes the API on port `8000`. Configure `LLM_BASE_URL`
with an address reachable from the API container. Do not expose `llama-server`
publicly without protection.

LLM settings can be overridden through environment variables. See
`.env.example` for the available values.

Test the integration:

```bash
curl -X POST http://localhost:8000/ai/test \
  -H "Content-Type: application/json" \
  -d '{"message":"Create a short invoice note for website design."}'
```

Expected response shape:

```json
{
  "answer": "Thank you for your business."
}
```

## Create an invoice

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

The backend calculates item amounts, subtotal, and total. It does not accept
client-provided totals or file paths.
