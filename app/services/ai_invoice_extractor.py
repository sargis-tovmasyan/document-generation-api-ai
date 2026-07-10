import json

from pydantic import ValidationError

from app.schemas import InvoiceDraft
from app.services.llm_client import LlmClient, llm_client

INVOICE_EXTRACTION_PROMPT = """Extract invoice data from the user message.
Return only JSON.
Fields: invoice_number, issue_date, due_date, currency, business_name,
business_email, business_address, client_name, client_email, client_address,
items, notes, payment_terms.
Use null for unknown fields. Dates must be YYYY-MM-DD. One service with one
amount means quantity is 1 and unit_price is that amount. Return one item per
service in items, each with description, quantity, and unit_price. Dollars
means USD. Rubles means RUB. Copy each item description exactly from the
service words in the message; do not use business or client words as services.
Message: __USER_MESSAGE__
JSON:
"""

INVOICE_GROUNDING_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": ["string", "null"]},
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
        content = await self.client.complete_prompt(prompt)

        raw_draft = _load_invoice_json(content)
        if not isinstance(raw_draft, dict):
            raise AiInvoiceParseError("LLM invoice response must be a JSON object")

        normalized_draft = normalize_flat_invoice_draft(raw_draft)
        grounded_draft = await ground_raw_invoice_draft(
            normalized_draft,
            user_message,
            self.client,
        )

        try:
            return InvoiceDraft.model_validate(grounded_draft)
        except ValidationError as error:
            raise AiInvoiceParseError("LLM returned an invalid invoice draft") from error


def normalize_flat_invoice_draft(raw_draft: dict) -> dict:
    raw_items = raw_draft.get("items")
    if isinstance(raw_items, list):
        items = [
            {
                "description": raw_item.get("description"),
                "quantity": raw_item.get("quantity"),
                "unit_price": raw_item.get("unit_price"),
            }
            for raw_item in raw_items
            if isinstance(raw_item, dict)
        ]
    else:
        items = [
            {
                "description": raw_draft.get("item_description"),
                "quantity": raw_draft.get("item_quantity"),
                "unit_price": raw_draft.get("item_unit_price"),
            }
        ]

    return {
        "document_type": "invoice",
        "invoice_number": raw_draft.get("invoice_number"),
        "issue_date": raw_draft.get("issue_date"),
        "due_date": raw_draft.get("due_date"),
        "currency": raw_draft.get("currency"),
        "template_language": raw_draft.get("template_language"),
        "business": {
            "name": raw_draft.get("business_name"),
            "email": raw_draft.get("business_email"),
            "address": raw_draft.get("business_address"),
        },
        "client": {
            "name": raw_draft.get("client_name"),
            "email": raw_draft.get("client_email"),
            "address": raw_draft.get("client_address"),
        },
        "items": items,
        "notes": raw_draft.get("notes"),
        "payment_terms": raw_draft.get("payment_terms"),
    }


async def ground_raw_invoice_draft(
    raw_draft: dict,
    user_message: str,
    client: LlmClient = llm_client,
) -> dict:
    prompt = (
        "Review the extracted invoice JSON against the original user message. "
        "Keep only values that are clearly supported by the user message or are safe normalizations, "
        "such as dollars to USD, rubles to RUB, dram to AMD, one service amount to quantity 1, "
        "or selecting template_language from an explicit language request or message language. "
        "Do not use regex-like substring rules. Reason semantically. "
        "Set unsupported or hallucinated values to null. Return JSON only.\n\n"
        f"User message:\n{user_message}\n\n"
        f"Extracted invoice JSON:\n{json.dumps(raw_draft, ensure_ascii=False)}\n\n"
        "Grounded invoice JSON:"
    )
    content = await client.complete_prompt(
        prompt,
        json_schema=INVOICE_GROUNDING_SCHEMA,
        max_tokens=1024,
        temperature=0.1,
    )
    grounded_draft = _load_invoice_json(content)
    if not isinstance(grounded_draft, dict):
        raise AiInvoiceParseError("LLM grounding response must be a JSON object")
    return grounded_draft


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
