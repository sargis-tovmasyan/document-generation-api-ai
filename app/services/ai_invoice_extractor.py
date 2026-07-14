import json

from pydantic import ValidationError

from app.schemas import InvoiceDraft
from app.services.llm_client import LlmClient, llm_client

INVOICE_EXTRACTION_PROMPT = """Extract a grounded invoice draft from the user message.
The user message is the only source of invoice facts. Return only JSON matching
the provided schema. Never invent examples, placeholders, defaults, parties,
addresses, emails, services, prices, dates, or payment terms.

For every value not explicitly supported by the message, return null. For items
not explicitly supported by the message, return an empty items array. A generic
request such as "Create a new invoice" must contain null invoice fields, null
business and client fields, and an empty items array.

Safe semantic normalizations are allowed: dollars to USD, rubles to RUB, dram to
AMD, dates to YYYY-MM-DD, and one explicitly priced service to quantity 1. Use
one item per service and preserve the service description from the message.
Choose template_language from an explicit language request or the language of
the message. Do not treat business or client words as services.

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
        "business": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "address": {"type": ["string", "null"]},
            },
            "required": ["name", "email", "address"],
            "additionalProperties": False,
        },
        "client": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "address": {"type": ["string", "null"]},
            },
            "required": ["name", "email", "address"],
            "additionalProperties": False,
        },
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
        "business",
        "client",
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

        raw_draft = _load_invoice_json(content)
        if not isinstance(raw_draft, dict):
            raise AiInvoiceParseError("LLM invoice response must be a JSON object")

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
