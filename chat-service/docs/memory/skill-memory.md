# Skill memory

Skill memory stores reusable document procedures. A skill describes how the assistant should perform a repeated workflow. It is not a fact about a user, business, or client.

## Example skill

```json
{
  "title": "Monthly maintenance invoice",
  "scope": "client",
  "trigger_text": "monthly maintenance invoice, recurring support invoice",
  "description": "Prepare the client's normal monthly support invoice.",
  "steps": [
    "Ask for the month if it is missing.",
    "Ask for the number of hours if it is missing.",
    "Use USD unless the user provides another currency.",
    "Use seven-day payment terms."
  ],
  "required_fields": ["month", "hours", "hourly_rate"],
  "confidence": 0.82,
  "status": "active"
}
```

Facts and skills are intentionally separate:

| Shared fact | Skill |
| --- | --- |
| "Client Alex normally uses USD." | "When creating Alex's monthly invoice, ask for month and hours, then use the normal defaults." |
| Describes what is known | Describes what to do |
| Stored as content and structured values | Stored as triggers, steps, and required fields |

## Learning flow

After a meaningful chat turn, the LLM learning extractor can propose up to three skills. Pydantic validates each candidate before storage.

Each skill stores:

- User, business, and client scope identifiers
- Source chat ID
- Scope: `user`, `business`, or `client`
- Title and description
- Trigger text
- Ordered steps
- Required fields
- Confidence and status
- Created, updated, and last-used timestamps

Candidates with confidence of at least `0.75` become active. Lower-confidence candidates use `needs_review`.

## Retrieval and use

The current store loads active skills for the supplied user, business, and client scope. Exact client and business matches receive higher priority.

When the context selector decides that saved context is needed, selected skills are added to the answer prompt as a title and description.

The skill does not execute code or bypass document validation. Invoice creation still uses the normal deterministic draft validation and invoice service.

## Status management

Use these endpoints:

- `GET /skill-memories`
- `PATCH /skill-memories/{skill_id}`

Supported statuses are `active`, `needs_review`, `disabled`, and `rejected`.

## Implementation details

- Skill candidate model and extraction: `app/services/learning_extractor.py`
- Skill storage and scoped retrieval: `app/services/knowledge_store.py`
- Management API: `app/routes/memories.py`
- Prompt context: `app/routes/ai_chat_memory.py`

## Current limitations

- Skills are retrieved by scope and ordering, not by matching the current message against `trigger_text`.
- The prompt currently receives the skill title and description, not the full step and required-field structure.
- There is no user interface for reviewing or editing skills.
- The caller-provided user scope is not authenticated.
- Skill creation has schema validation but no separate deterministic policy approval layer.
- Audit events are written for creation only.

