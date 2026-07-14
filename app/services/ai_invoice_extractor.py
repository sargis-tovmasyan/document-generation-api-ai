import json

from pydantic import ValidationError

from app.schemas import InvoiceDraft
from app.services.llm_client import LlmClient, llm_client

INVOICE_EXTRACTION_PROMPT = """Extract only explicit invoice facts into the JSON schema. Never invent values.
Use null for unknown fields and [] when no items are stated. A generic invoice
request has no facts. Copy the full invoice identifier. Map issued/issue date to
issue_date, due date to due_date, sender/from party to business_name, and
customer/for client party to client_name. Items contain only stated products or
services; description excludes parties, dates, currency, quantity, and price.
Map stated count and per-unit price. Normalize dates to YYYY-MM-DD and currency
names/symbols to three-letter codes. Infer template_language from the message.
Return JSON only and include every explicit fact.

User message:
__USER_MESSAGE__

Invoice JSON:
"""

INVOICE_GROUNDING_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["invoice"]},
        "invoice_number": {"type": ["string", "null"]},
        "issue_date": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "template_language": {"type": "string", "enum": ["en", "ru"]},
        "business_name": {"type": ["string", "null"]},
        "business_email": {"type": ["string", "null"]},
        "business_address": {"type": ["string", "null"]},
        "client_name": {"type": ["string", "null"]},
        "client_email": {"type": ["string", "null"]},
        "client_address": {"type": ["string", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": ["string", "null"]},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                },
                "required": ["description", "quantity", "unit_price"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": ["string", "null"]},
        "payment_terms": {"type": ["string", "null"]},
    },
    "required": [
        "document_type",
        "invoice_number",
        "issue_date",
        "due_date",
        "currency",
        "template_language",
        "business_name",
        "business_email",
        "business_address",
        "client_name",
        "client_email",
        "client_address",
        "items",
        "notes",
        "payment_terms",
    ],
    "additionalProperties": False,
}


class AiInvoiceParseError(Exception):
    """Raised when the LLM output cannot be validated as an invoice draft."""


class AiInvoiceExtractor:
    def __init__(self, client: LlmClient = llm_client) -> None:
        self.client = client

    async def extract(self, user_message: str) -> InvoiceDraft:
        prompt = INVOICE_EXTRACTION_PROMPT.replace(
            "__USER_MESSAGE__",
            user_message,
        )
        content = await self.client.complete_prompt(
            prompt,
            json_schema=INVOICE_GROUNDING_SCHEMA,
            max_tokens=512,
            temperature=0.1,
        )

        flat_draft = _load_invoice_json(content)
        raw_draft = {
            "document_type": flat_draft.get("document_type"),
            "invoice_number": flat_draft.get("invoice_number"),
            "issue_date": flat_draft.get("issue_date"),
            "due_date": flat_draft.get("due_date"),
            "currency": flat_draft.get("currency"),
            "template_language": flat_draft.get("template_language"),
            "business": {
                "name": flat_draft.get("business_name"),
                "email": flat_draft.get("business_email"),
                "address": flat_draft.get("business_address"),
            },
            "client": {
                "name": flat_draft.get("client_name"),
                "email": flat_draft.get("client_email"),
                "address": flat_draft.get("client_address"),
            },
            "items": flat_draft.get("items", []),
            "notes": flat_draft.get("notes"),
            "payment_terms": flat_draft.get("payment_terms"),
        }

        try:
            return InvoiceDraft.model_validate(raw_draft)
        except ValidationError as error:
            raise AiInvoiceParseError("LLM returned an invalid invoice draft") from error


def _load_invoice_json(content: str) -> dict:
    try:
        raw_draft = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise AiInvoiceParseError("LLM returned invalid invoice JSON")
        try:
            raw_draft = json.loads(content[start : end + 1])
        except json.JSONDecodeError as error:
            raise AiInvoiceParseError("LLM returned invalid invoice JSON") from error

    if not isinstance(raw_draft, dict):
        raise AiInvoiceParseError("LLM invoice response must be a JSON object")
    return raw_draft


ai_invoice_extractor = AiInvoiceExtractor()
