# Session memory

Session memory keeps temporary state for one chat thread. It allows a conversation to continue across multiple messages without asking the user to repeat information.

## What it stores

Session memory is stored as JSON in the `session_memories` table. A record belongs to exactly one `chat_id`.

Common fields include:

```json
{
  "active_document_type": "invoice",
  "current_intent": "create_invoice",
  "draft": {
    "invoice_number": "INV-005",
    "client": {
      "name": "Grill.am"
    }
  },
  "missing_fields": ["issue_date", "currency", "items"],
  "last_document_id": null,
  "requested_memories": ["number 42"]
}
```

The JSON structure can grow as new document types are added, but document-specific fields must remain inside the current chat.

## Invoice continuation

When an invoice request is incomplete, the assistant:

1. Extracts the fields that the user provided.
2. Stores the partial draft and missing-field list in session memory.
3. Returns `status: "missing_fields"` to the frontend.
4. Merges the next user submission into the stored draft.
5. Creates the invoice when all required fields are valid.

After successful creation, the backend clears:

- `active_document_type`
- `current_intent`
- `draft`
- `missing_fields`

It keeps `last_document_id`, chat messages, and other non-document session values.

## Explicit temporary memory

Requests such as "remember number 42" are stored in `requested_memories` for the current chat. A different chat cannot recall that value.

If the user asks the assistant to remember a value but does not provide it, the session can store `pending_memory_request`. The next message supplies the value.

These temporary values are deliberately excluded from shared-memory learning.

## Recent conversation context

Chat messages are stored separately in `chat_messages`. For follow-up questions, the context selector can include up to the recent messages loaded by the chat route.

Example:

```text
User: Name five flowers and number them.
Assistant: 1. Rose, 2. Sunflower, 3. Tulip, 4. Daisy, 5. Lily.
User: What was the third flower?
Assistant: Tulip.
```

The flower list comes from recent chat history. It is not saved as shared memory.

## API usage

Start a chat without a `chat_id`:

```json
{
  "message": "Create invoice INV-005 for Grill.am"
}
```

The response contains a generated `chat_id`. Send that ID with later messages:

```json
{
  "chat_id": "chat_example",
  "message": "Use AMD and today's date"
}
```

Session endpoints:

- `GET /chat-threads/{chat_id}/session-memory`
- `DELETE /chat-threads/{chat_id}/session-memory/document-scope`

## Implementation details

- Schema: `app/services/chat_schema.py`
- Persistence: `get_session_state`, `upsert_session_state`, and `clear_document_scope` in `app/services/chat_store.py`
- Invoice continuation: `app/routes/ai_chat_memory.py`
- Draft completion endpoint: `app/routes/invoices.py`

## Current limitations

- Session access is not protected by authentication.
- `chat_id` ownership is not checked against an authenticated user.
- Session expiration is represented in the schema but is not enforced.
- Session state is schemaless JSON and has no migration/version compatibility layer beyond the database version counter.

