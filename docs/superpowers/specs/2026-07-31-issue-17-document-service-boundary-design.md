# Issue #17: Document Service Boundary — First Slice

## Goal

Begin issue #17 without performing the full service split in one change. Establish a versioned document contract and a framework-independent document application boundary while preserving every current public HTTP response and keeping the backend deployable as one process.

Issue #15 is not a prerequisite for this slice. LangChain/LangGraph changes remain inside the chat domain and can be introduced later without changing the document contract created here.

## Scope

This slice delivers:

- a versioned `documents.v1` Protocol Buffers contract for the document capabilities described in issue #17
- a document application package that owns invoice extraction, validation, completion, creation, listing, reset, and download lookup use cases
- thin FastAPI document routes that translate HTTP requests and responses to the application boundary
- contract and compatibility tests proving the existing HTTP behavior remains stable
- an explicit dependency inventory documenting the remaining direct chat-to-document imports

This slice intentionally keeps the application boundary in-process. The second process, gRPC server/client runtime, separate storage, and deployment split come in later issue #17 slices.

## Dependency Decision

Issue #15 and issue #17 are complementary:

- Issue #15 may replace the current chat router with LangGraph nodes and typed tools.
- Issue #17 defines which document capabilities those nodes or tools are allowed to call.

The document boundary does not depend on LangGraph. Defining it first gives both the current orchestrator and a future LangGraph orchestrator the same stable interface. Existing tests already cover the main issue #15 regression scenarios, including memory recall, BBQ routing, letter counting, recent context, partial invoice continuation, and streaming cleanup.

## Architecture

### Versioned contract

Create:

```text
contracts/documents/v1/document_service.proto
```

The package is `documents.v1`. It defines the initial service methods from issue #17:

```proto
service DocumentService {
  rpc ExtractDraft(ExtractDraftRequest) returns (ExtractDraftResponse);
  rpc CompleteDraft(CompleteDraftRequest) returns (CompleteDraftResponse);
  rpc CreateDocument(CreateDocumentRequest) returns (CreateDocumentResponse);
  rpc GetDocument(GetDocumentRequest) returns (GetDocumentResponse);
  rpc ListDocuments(ListDocumentsRequest) returns (ListDocumentsResponse);
  rpc GetDownloadDescriptor(GetDownloadDescriptorRequest) returns (GetDownloadDescriptorResponse);
}
```

Contract rules:

- Every request includes `request_id` and `document_type` where applicable.
- Create requests include an `idempotency_key`, even though persistence enforcement is deferred to a later slice.
- Money is represented as decimal-safe strings, never protobuf floating-point fields.
- Dates use ISO `YYYY-MM-DD` strings at this boundary.
- Draft responses include machine-readable missing field keys.
- Internal database rows and local filesystem paths are never exposed.
- Download responses expose a stable document ID, filename, media type, and delivery path/URL descriptor.

Generated Python code is not committed in this slice. CI compiles the proto to a temporary directory to prove the contract is valid and ready for generated clients in the next gRPC-runtime slice.

### Document application package

Create a focused package:

```text
app/documents/
  __init__.py
  application.py
  errors.py
  models.py
```

`models.py` contains transport-neutral input and result models for document operations. The models may reuse validated invoice schema types internally, but they must not import FastAPI response classes, `JSONResponse`, `HTTPException`, or gRPC context types.

`errors.py` defines document boundary errors:

- `DocumentConflictError`
- `DocumentNotFoundError`
- `DocumentExtractionUnavailableError`
- `DocumentExtractionInvalidError`
- `DocumentItemsInvalidError`

`application.py` exposes one injectable `DocumentApplicationService`. It coordinates the existing extractor, deterministic validators, invoice service, and download lookup. Existing lower-level services remain the implementation details during this migration slice.

The application service returns typed results for:

- draft ready versus missing fields
- document created
- document lists
- download descriptors
- development-only reset results

It never returns an HTTP response or raises an HTTP-specific exception.

### HTTP compatibility adapter

The existing routes remain externally unchanged:

- `POST /ai/invoice/extract`
- `POST /ai/invoice/generate`
- `POST /invoices/draft/complete`
- `POST /invoices`
- `GET /invoices`
- `DELETE /invoices`
- `GET /invoices/{invoice_id}/download`

`app/routes/ai_invoice.py` and `app/routes/invoices.py` become adapters. They call the document application service, translate typed results into the existing Pydantic response models, and map document errors to the current status codes and messages.

The frontend, route paths, response JSON, status codes, PDF URLs, and static `/generated` mount remain unchanged.

## Data Flow

For extraction:

```text
HTTP request
  -> FastAPI route validates request
  -> DocumentApplicationService.extract_draft
  -> existing AI extractor
  -> deterministic missing-field validation
  -> typed application result
  -> route maps to current HTTP response
```

For creation or completion:

```text
HTTP request
  -> DocumentApplicationService
  -> normalize items when required
  -> deterministic validation
  -> existing invoice persistence/PDF service
  -> typed created result or document error
  -> route maps to current HTTP response/status
```

For download:

```text
HTTP request
  -> DocumentApplicationService.get_download_descriptor
  -> existing invoice lookup
  -> descriptor or not-found error
  -> route returns the existing FileResponse behavior
```

## Error Handling

The application boundary owns semantic failures; transports own presentation:

- extraction backend unavailable -> `DocumentExtractionUnavailableError` -> existing HTTP 503 body
- invalid extraction output -> `DocumentExtractionInvalidError` -> existing HTTP 422 body
- invalid raw invoice items -> `DocumentItemsInvalidError` -> existing HTTP 422 detail
- duplicate invoice number -> `DocumentConflictError` -> existing HTTP 409 detail
- missing invoice or missing generated file -> `DocumentNotFoundError` -> existing HTTP 404 detail

The later gRPC server will map the same errors to gRPC status codes without moving domain logic into the server implementation.

## Chat Coupling

This slice records but does not yet remove direct document imports from `app/routes/ai_chat.py` and `app/routes/ai_chat_memory.py`. Migrating them safely requires the gRPC client/application port introduced in a later issue #17 phase.

The first slice must not change chat session draft ownership, chat response shapes, memory behavior, or streaming behavior.

## Testing

Add tests at three levels:

1. Contract validation
   - compile `document_service.proto` with the current Python gRPC tooling
   - assert the package and required RPC methods are present
   - assert authoritative money fields use strings

2. Document application tests
   - extraction ready and missing-field results
   - extraction unavailable and invalid-output errors
   - draft completion and raw-item normalization
   - duplicate invoice conflict mapping
   - list and download descriptor behavior

3. HTTP compatibility tests
   - preserve current response bodies and status codes for extraction, missing fields, creation, conflicts, listing, and download failures
   - ensure route tests patch the application boundary rather than lower-level persistence or LLM functions

Run the complete existing pytest suite after the new tests. The current 98-test baseline must remain green.

## Deployment and Storage

The current Docker Compose topology, SQLite database, generated file directory, and single API container remain unchanged in this slice. No new port or process is introduced yet.

The user's staged `docker-compose.yml` project-name change is unrelated and must not be modified or included in issue #17 commits.

## Follow-up Slices

After this slice:

1. Generate and package the Python gRPC client/server modules.
2. Add a separate Document Service process using the same application service.
3. Introduce a Chat Orchestrator gRPC client with deadlines, cancellation, correlation IDs, and error mapping.
4. Move draft authority and document persistence to the Document Service.
5. Route public document HTTP paths to the Document Service and remove direct chat imports.
6. Split databases, containers, CI jobs, and deployments.

Issue #15 can proceed independently and later call the same document application/gRPC boundary through typed LangGraph tools.

## Acceptance Criteria

- The `documents.v1` proto compiles successfully.
- Document use cases used by public document routes are available through a framework-independent application service.
- FastAPI document routes contain transport mapping but no persistence, PDF, extraction, or validation orchestration.
- Existing public document routes and responses remain compatible.
- The complete existing pytest suite and new contract/application tests pass.
- Chat behavior and deployment topology remain unchanged.
- No gRPC runtime server, storage split, MCP adapter, or LangGraph refactor is introduced in this slice.
