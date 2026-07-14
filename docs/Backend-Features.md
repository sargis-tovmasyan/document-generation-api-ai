# Backend Features

This page explains what the Document Generation API can currently do. It is written for developers, testers, and product stakeholders who need a clear overview without reading the source code.

## At a glance

The backend combines conversational AI, invoice creation, PDF generation, chat history, and memory management. Most user-facing flows begin through the chat endpoint, while direct invoice endpoints are available for integrations that do not need a conversational interface.

## Feature catalog

| Feature | What it does | Endpoint |
|---|---|---|
| Service health check | Confirms that the backend is running and able to respond. Useful for uptime checks and deployment verification. | `GET /health` |
| AI chat | Accepts a natural-language message and decides whether to answer a question, remember information, recall saved information, list invoices, or start/continue invoice creation. | `POST /ai/chat` |
| Streaming AI chat | Provides the same conversational behavior as the normal chat endpoint, but streams the answer as Server-Sent Events so the frontend can display text while it is being generated. | `POST /ai/chat/stream` |
| Recent conversation context | Uses recent messages from the current chat when a follow-up question depends on earlier parts of the conversation. | Built into `POST /ai/chat` and `POST /ai/chat/stream` |
| Chat-scoped memory | Remembers information the user explicitly asks the assistant to keep during a chat, then recalls it later when relevant. | Built into `POST /ai/chat` and `POST /ai/chat/stream` |
| Shared memory | Stores reusable facts associated with a user and makes them available across conversations. | `GET /shared-memories` and `PATCH /shared-memories/{memory_id}` |
| Skill memory | Stores learned skills or reusable behavioral knowledge that can support later answers. | `GET /skill-memories` and `PATCH /skill-memories/{skill_id}` |
| Memory review status | Allows a stored memory to be marked as `active`, `needs_review`, `disabled`, or `rejected`. | `PATCH /shared-memories/{memory_id}` and `PATCH /skill-memories/{skill_id}` |
| Automatic learning from conversations | Extracts useful reusable information from completed chat turns and stores it for future use. | Internal behavior; triggered during chat processing |
| Invoice intent detection | Recognizes when a user is asking to create an invoice, even when the request is written conversationally. | Built into `POST /ai/chat` and `POST /ai/chat/stream` |
| Invoice draft extraction | Converts a natural-language invoice request into a structured draft without creating the final invoice. It also reports which required fields are still missing. | `POST /ai/invoice/extract` |
| Invoice generation from text | Converts a natural-language request into an invoice and creates the PDF when enough information is available. If information is missing, it returns the missing fields instead of generating an incomplete document. | `POST /ai/invoice/generate` |
| Multi-message invoice completion | Keeps an incomplete invoice draft in the chat session so the user can provide missing information in later messages. | Built into `POST /ai/chat` and `POST /ai/chat/stream` |
| Complete a structured invoice draft | Accepts a draft that may have been filled by a form or previous AI step, validates it, and creates the final invoice when complete. | `POST /invoices/draft/complete` |
| Natural-language item normalization | Understands item text such as “2 hours of consulting at 5000 each” and converts it into structured descriptions, quantities, and unit prices. | Used by `POST /invoices/draft/complete` |
| Direct invoice creation | Creates an invoice from a fully structured request without using AI extraction. Suitable for trusted integrations and administrative tools. | `POST /invoices` |
| Invoice list | Returns all stored invoices with their main details. | `GET /invoices` |
| Invoice PDF download | Returns the generated invoice as a downloadable PDF file. | `GET /invoices/{invoice_id}/download` |
| Generated-file hosting | Serves generated documents from the backend’s generated-files directory. | `GET /generated/{file_path}` |
| Reset invoice storage | Deletes the current invoice records through the invoice store reset operation. This is mainly intended for development or controlled administration and should not be exposed casually in production. | `DELETE /invoices` |
| Create a chat thread | Creates a new conversation container for a user, optionally linked to a business profile or client. | `POST /chat-threads` |
| List chat threads | Returns active, archived, or deleted chat threads for a user. | `GET /chat-threads` |
| Read a chat thread | Returns one thread together with its messages and current session state. | `GET /chat-threads/{chat_id}` |
| Update a chat thread | Renames, archives, pins, or reorders a chat thread. | `PATCH /chat-threads/{chat_id}` |
| Delete a chat thread | Soft-deletes a chat thread so it is removed from the active list without immediately erasing its stored data. | `DELETE /chat-threads/{chat_id}` |
| Read thread messages | Returns the messages stored inside a chat thread. | `GET /chat-threads/{chat_id}/messages` |
| Read session memory | Returns the current temporary state for a chat, including an unfinished document draft when one exists. | `GET /chat-threads/{chat_id}/session-memory` |
| Clear document-specific session state | Removes the active document draft and related invoice context while keeping the chat thread itself. | `DELETE /chat-threads/{chat_id}/session-memory/document-scope` |
| User UI settings | Stores and retrieves frontend preferences associated with a user. | `GET /user-ui-settings` and `PATCH /user-ui-settings` |
| API validation | Validates request and response data with typed schemas and returns clear validation errors instead of silently accepting malformed data. | Applied across API endpoints |
| Duplicate invoice protection | Prevents two invoices from being created with the same invoice number and returns HTTP `409 Conflict`. | Applied to invoice creation endpoints |
| Request logging and tracing | Records structured request, invoice, and AI processing events to make failures and production behavior easier to investigate. | Internal behavior |
| Interactive API documentation | FastAPI automatically exposes machine-readable and interactive API documentation. | `GET /docs`, `GET /redoc`, and `GET /openapi.json` |

## Main user flows

### Ask a normal question

Send a message to `POST /ai/chat`. The backend decides that the request is a normal answer, selects only the relevant recent context or saved memory, and returns a user-facing response.

### Create an invoice through chat

1. Send the invoice request to `POST /ai/chat`.
2. The backend extracts a draft and checks required fields.
3. When details are missing, the response contains `status: "missing_fields"` and the current draft is kept in session memory.
4. Send the missing information in the same chat.
5. When the draft is complete, the backend creates the invoice and returns its ID, totals, currency, and PDF download URL.

### Create an invoice without chat

Use `POST /ai/invoice/generate` for a natural-language request, or `POST /invoices` when the calling application already has fully structured invoice data.

### Continue an unfinished invoice from a form

Use `POST /invoices/draft/complete`. The backend validates the draft, normalizes any natural-language item list, and either returns the remaining missing fields or creates the invoice.

## Response behavior worth knowing

- `200 OK` usually means the request was handled successfully.
- `201 Created` is returned when a direct invoice is created.
- `409 Conflict` means the invoice number already exists.
- `422 Unprocessable Entity` means the request or AI-produced data could not be validated.
- `503 Service Unavailable` means the configured language model is temporarily unavailable.
- Chat responses use a `status` field such as `answer`, `missing_fields`, `created`, `invoice_list`, `llm_unavailable`, or `ai_parse_error` so the frontend can choose the correct UI.

## Current scope

The backend currently focuses on invoices as its document type. The architecture already separates chat, memory, document extraction, validation, storage, and PDF generation, which provides a foundation for adding more document types later.

## Production notes

- Protect administrative or destructive endpoints, especially `DELETE /invoices`, before exposing the service publicly.
- Configure authentication and tenant isolation before using the default user behavior in a multi-user production system.
- Keep the language-model service, database, and generated-file storage monitored because chat and invoice generation depend on them.
- Treat `/docs` and `/redoc` as developer references; this page is the human-readable product overview.
