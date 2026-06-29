import json
import re
from decimal import Decimal

from pydantic import ValidationError

from app.schemas import InvoiceDraft, InvoiceTemplateLanguage
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
        grounded_draft = ground_raw_invoice_draft(normalized_draft, user_message)

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


def ground_raw_invoice_draft(
    raw_draft: dict,
    user_message: str,
) -> dict:
    message_lower = user_message.lower()
    raw_business = raw_draft.get("business")
    raw_client = raw_draft.get("client")
    raw_business = raw_business if isinstance(raw_business, dict) else {}
    raw_client = raw_client if isinstance(raw_client, dict) else {}
    client_name = _ground_raw_text(
        raw_client.get("name"),
        user_message,
    ) or _extract_role_name(user_message, ("for", "to"))
    business_name = _extract_business_name(
        raw_business.get("name"),
        user_message,
    ) or _extract_role_name(user_message, ("from",))

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
    if currency is None:
        currency = _extract_currency(user_message)

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
        "template_language": _select_template_language(user_message),
        "business": {
            "name": business_name,
            "email": _ground_raw_text(
                raw_business.get("email"),
                user_message,
            ),
            "address": _extract_labeled_text(
                raw_business.get("address"),
                user_message,
                r"(?:my\s+)?business\s+address",
            ),
        },
        "client": {
            "name": client_name,
            "email": _ground_raw_text(
                raw_client.get("email"),
                user_message,
            ),
            "address": _extract_labeled_text(
                raw_client.get("address"),
                user_message,
                r"client\s+address",
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


def _extract_currency(user_message: str) -> str | None:
    message_lower = user_message.lower()
    if "dollar" in message_lower or "$" in user_message:
        return "USD"
    if "euro" in message_lower or "€" in user_message:
        return "EUR"
    if "pound" in message_lower or "£" in user_message:
        return "GBP"
    if "dram" in message_lower or "֏" in user_message:
        return "AMD"
    if any(
        marker in message_lower
        for marker in ("ruble", "rouble", "rubles", "roubles", "руб", "рублей")
    ) or "₽" in user_message:
        return "RUB"
    return None


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


def _extract_labeled_text(
    value: object,
    user_message: str,
    label_pattern: str,
) -> str | None:
    grounded_value = _ground_raw_text(value, user_message)
    if grounded_value is None:
        return None

    pattern = rf"\b{label_pattern}\s+(?:is\s+)?{re.escape(grounded_value)}(?=[.;]|$)"
    if re.search(pattern, user_message, flags=re.IGNORECASE):
        return grounded_value
    return None


def _extract_business_name(value: object, user_message: str) -> str | None:
    grounded_value = _ground_raw_text(value, user_message)
    if grounded_value is None:
        return None

    pattern = (
        r"\b(?:my\s+)?business\s+name\s+(?:is\s+)?"
        rf"{re.escape(grounded_value)}(?=[,.;]|$)"
    )
    if re.search(pattern, user_message, flags=re.IGNORECASE):
        return grounded_value
    return None


def _select_template_language(user_message: str) -> InvoiceTemplateLanguage:
    explicit_language = _detect_explicit_template_language(user_message)
    if explicit_language is not None:
        return explicit_language

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
