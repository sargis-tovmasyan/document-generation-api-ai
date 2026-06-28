import json
import re
from decimal import Decimal

from pydantic import ValidationError

from app.schemas import InvoiceDraft, InvoiceTemplateLanguage
from app.services.llm_client import LlmClient, llm_client

INVOICE_EXTRACTION_PROMPT = """Extract invoice data from the user message.
Return only one JSON object matching these fields:
document_type, invoice_number, issue_date, due_date, currency, template_language,
business{name,email,address}, client{name,email,address},
items[{description,quantity,unit_price}], notes, payment_terms, missing_fields.

Rules:
- Output compact JSON without indentation or extra whitespace.
- Copy or directly normalize only information stated by the user.
- Omit unknown fields. Never invent example values.
- Dates must use ISO format YYYY-MM-DD when the user provides a date.
- template_language must be "en" for English invoice text or "ru" for Russian invoice text.
- Choose template_language from the user's message language or explicit words like English, Russian, на русском, or на английском.
- "for NAME" or "to NAME" identifies the client. "from NAME" identifies the business.
- For one service with one amount, quantity is 1 and unit_price is that amount.
- Include exactly one item per service. Never duplicate items.
- "dollars" or "$" means USD.
- "rubles", "roubles", "ruble", "руб", "рублей", or "₽" means RUB.
- missing_fields may be an empty array; the backend calculates it.

User:
__USER_MESSAGE__
"""

INVOICE_EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["invoice"]},
        "invoice_number": {"type": ["string", "null"]},
        "issue_date": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "template_language": {"type": ["string", "null"], "enum": ["ru", "en", None]},
        "business": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "address": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "client": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "address": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "items": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": ["string", "null"]},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                },
                "additionalProperties": False,
            },
        },
        "notes": {"type": ["string", "null"]},
        "payment_terms": {"type": ["string", "null"]},
        "missing_fields": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string"},
        },
    },
    "required": ["document_type"],
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
            json_schema=INVOICE_EXTRACTION_JSON_SCHEMA,
        )

        try:
            raw_draft = json.loads(content)
        except json.JSONDecodeError as error:
            raise AiInvoiceParseError("LLM returned invalid invoice JSON") from error

        if not isinstance(raw_draft, dict):
            raise AiInvoiceParseError("LLM invoice response must be a JSON object")

        raw_draft.pop("missing_fields", None)
        grounded_draft = ground_raw_invoice_draft(raw_draft, user_message)

        try:
            return InvoiceDraft.model_validate(grounded_draft)
        except ValidationError as error:
            raise AiInvoiceParseError("LLM returned an invalid invoice draft") from error


def ground_raw_invoice_draft(
    raw_draft: dict,
    user_message: str,
) -> dict:
    message_lower = user_message.lower()
    client_name = _extract_role_name(user_message, ("for", "to"))
    business_name = _extract_role_name(user_message, ("from",))
    raw_business = raw_draft.get("business")
    raw_client = raw_draft.get("client")
    raw_business = raw_business if isinstance(raw_business, dict) else {}
    raw_client = raw_client if isinstance(raw_client, dict) else {}

    grounded_items: list[dict] = []
    seen_items: set[tuple[str | None, Decimal | None]] = set()
    raw_items = raw_draft.get("items")
    raw_items = raw_items if isinstance(raw_items, list) else []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        raw_description = raw_item.get("description")
        description = (
            raw_description.strip()
            if isinstance(raw_description, str)
            and _description_is_grounded(raw_description, user_message)
            else None
        )
        raw_unit_price = _decimal_or_none(raw_item.get("unit_price"))
        unit_price = (
            raw_unit_price
            if _number_is_grounded(raw_unit_price, user_message)
            else None
        )
        quantity = _decimal_or_none(raw_item.get("quantity"))
        if quantity is not None and not _number_is_grounded(quantity, user_message):
            quantity = None
        if unit_price is not None and quantity == unit_price:
            quantity = Decimal("1")
        if quantity is None and description and unit_price is not None:
            quantity = Decimal("1")

        item_key = (description.lower() if description else None, unit_price)
        if item_key in seen_items:
            continue
        seen_items.add(item_key)
        grounded_items.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
            }
        )

    raw_currency = raw_draft.get("currency")
    currency = (
        raw_currency.strip().upper()
        if isinstance(raw_currency, str) and raw_currency.strip()
        else None
    )
    if currency and currency.lower() not in message_lower:
        currency_words = {
            "USD": ("dollar", "$"),
            "EUR": ("euro", "€"),
            "GBP": ("pound", "£"),
            "AMD": ("dram", "֏"),
            "RUB": ("ruble", "rouble", "rubles", "roubles", "руб", "рублей", "₽"),
        }
        if not any(
            marker in message_lower
            for marker in currency_words.get(currency, ())
        ):
            currency = None

    return {
        "document_type": raw_draft.get("document_type"),
        "invoice_number": _ground_raw_text(
            raw_draft.get("invoice_number"),
            user_message,
        ),
        "issue_date": _ground_raw_text(
            raw_draft.get("issue_date"),
            user_message,
        ),
        "due_date": _ground_raw_text(
            raw_draft.get("due_date"),
            user_message,
        ),
        "currency": currency,
        "template_language": _select_template_language(
            raw_draft.get("template_language"),
            user_message,
        ),
        "business": {
            "name": business_name,
            "email": _ground_raw_text(
                raw_business.get("email"),
                user_message,
            ),
            "address": _ground_raw_text(
                raw_business.get("address"),
                user_message,
            ),
        },
        "client": {
            "name": client_name,
            "email": _ground_raw_text(
                raw_client.get("email"),
                user_message,
            ),
            "address": _ground_raw_text(
                raw_client.get("address"),
                user_message,
            ),
        },
        "items": grounded_items,
        "notes": _ground_raw_text(raw_draft.get("notes"), user_message),
        "payment_terms": _ground_raw_text(
            raw_draft.get("payment_terms"),
            user_message,
        ),
    }


def _ground_raw_text(value: object, user_message: str) -> str | None:
    if isinstance(value, str) and value and value.lower() in user_message.lower():
        return value.strip()
    return None


def _select_template_language(
    raw_value: object,
    user_message: str,
) -> InvoiceTemplateLanguage:
    explicit_language = _detect_explicit_template_language(user_message)
    if explicit_language is not None:
        return explicit_language

    if isinstance(raw_value, str) and raw_value.strip().lower() in {"ru", "en"}:
        return raw_value.strip().lower()  # type: ignore[return-value]

    if re.search(r"[а-яё]", user_message.lower()):
        return "ru"
    return "en"


def _detect_explicit_template_language(user_message: str) -> InvoiceTemplateLanguage | None:
    message_lower = user_message.lower()
    if any(marker in message_lower for marker in ("english", "англий")):
        return "en"
    if any(marker in message_lower for marker in ("russian", "русск")):
        return "ru"
    return None


def _extract_role_name(
    user_message: str,
    role_words: tuple[str, ...],
) -> str | None:
    roles = "|".join(re.escape(word) for word in role_words)
    match = re.search(
        rf"\b(?:{roles})\s+(.+?)(?=\s+(?:for|from|about)\b|[,.;]|$)",
        user_message,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    name = match.group(1).strip()
    if len(name.split()) > 5:
        return None
    return name


def _number_is_grounded(
    value: Decimal | None,
    user_message: str,
) -> bool:
    if value is None:
        return False
    numbers = {
        Decimal(match)
        for match in re.findall(r"\d+(?:\.\d+)?", user_message)
    }
    return value in numbers


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _description_is_grounded(
    description: str | None,
    user_message: str,
) -> bool:
    if not description:
        return False

    message_words = re.findall(r"[a-zа-яё0-9]+", user_message.lower())
    description_words = re.findall(r"[a-zа-яё0-9]+", description.lower())
    if not description_words:
        return False

    return all(
        any(
            message_word.startswith(description_word)
            or description_word.startswith(message_word)
            for message_word in message_words
        )
        for description_word in description_words
    )


ai_invoice_extractor = AiInvoiceExtractor()
