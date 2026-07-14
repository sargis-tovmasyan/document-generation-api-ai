import json

from pydantic import ValidationError

from app.schemas import InvoiceDraft
from app.services.llm_client import LlmClient, llm_client

INVOICE_EXTRACTION_PROMPT = """Extract invoice facts from the user message into the flat JSON schema.
The user message is the only source of invoice facts. Return only JSON matching
the provided schema. Never invent examples, placeholders, defaults, parties,
addresses, emails, services, prices, dates, or payment terms.

Map every explicit fact before returning JSON:
- invoice_number: copy the complete identifier associated with the invoice;
  preserve every letter, digit, and hyphen.
- issue_date: the date described as issued or issue date.
- due_date: the date described as due or due date.
- business_name: the sender, seller, business, or party following "from".
- client_name: the customer, buyer, client, or party following "for client".
- items: only products or services explicitly requested. Each description must
  contain only the product or service name, not client, business, date, currency,
  quantity, or price text. Map explicit count to quantity and per-unit amount to
  unit_price.

For every value not explicitly supported by the message, return null. For items
not explicitly supported by the message, return an empty items array. A generic
request such as "Create a new invoice" must contain null invoice fields, null
business and client fields, and an empty items array.

Safe semantic normalizations are allowed: dollars to USD, rubles to RUB, dram to
AMD, dates to YYYY-MM-DD, and an explicitly singular item to quantity 1. Choose
template_language from an explicit language request or the language of the
message. Do not treat business or client words as services.

Example user message:
Create invoice INV-913 from QA Studio for client Gamma LLC, issued 2026-07-14,
due 2026-07-21, USD, one website audit x1 at 125 USD.
Example JSON:
{"document_type":"invoice","invoice_number":"INV-913","issue_date":"2026-07-14","due_date":"2026-07-21","currency":"USD","template_language":"en","business_name":"QA Studio","business_email":null,"business_address":null,"client_name":"Gamma LLC","client_email":null,"client_address":null,"items":[{"description":"website audit","quantity":1,"unit_price":125}],"notes":null,"payment_terms":null}

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
