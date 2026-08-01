import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.config import (
    APP_LOG_DEBUG_PAYLOADS,
    APP_LOG_FRONTEND_MESSAGES,
    APP_LOG_LLM_RAW,
    APP_LOG_MAX_FIELD_LENGTH,
    APP_LOG_RESPONSE_BODY,
    DEPLOYMENT_ENVIRONMENT,
    SERVICE_NAME,
)
from app.observability import get_request_id, get_trace_id

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "key",
)

logger = logging.getLogger("app.events")


def log_event(
    event: str,
    level: int = logging.INFO,
    message: str | None = None,
    debug_payload: bool = False,
    **fields: Any,
) -> None:
    if debug_payload and not APP_LOG_DEBUG_PAYLOADS:
        return

    payload: dict[str, Any] = {
        "event": event,
        "service_name": SERVICE_NAME,
        "environment": DEPLOYMENT_ENVIRONMENT,
        "request_id": get_request_id(),
        "trace_id": get_trace_id(),
    }
    if message is not None:
        payload["message"] = message
    payload.update(fields)

    logger.log(
        level,
        json.dumps(_sanitize(payload), ensure_ascii=False, sort_keys=True),
    )


def include_frontend_message(message: str) -> dict[str, Any]:
    result: dict[str, Any] = {"message_length": len(message)}
    if APP_LOG_FRONTEND_MESSAGES or APP_LOG_DEBUG_PAYLOADS:
        result["frontend_message"] = message
    return result


def include_response_body(body: Any) -> dict[str, Any]:
    if not APP_LOG_RESPONSE_BODY and not APP_LOG_DEBUG_PAYLOADS:
        return {}
    return {"response_body": body}


def include_llm_payload(
    *,
    prompt: str | None = None,
    raw_response: str | None = None,
) -> dict[str, Any]:
    if not APP_LOG_LLM_RAW and not APP_LOG_DEBUG_PAYLOADS:
        return {}

    result: dict[str, Any] = {}
    if prompt is not None:
        result["llm_prompt"] = prompt
    if raw_response is not None:
        result["llm_raw_response"] = raw_response
    return result


def summarize_invoice_draft(draft: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    data = _model_to_dict(draft)
    business = _mapping_or_empty(data.get("business"))
    client = _mapping_or_empty(data.get("client"))
    items = data.get("items") if isinstance(data.get("items"), list) else []

    return {
        "invoice_number": data.get("invoice_number"),
        "issue_date": data.get("issue_date"),
        "due_date": data.get("due_date"),
        "currency": data.get("currency"),
        "template_language": data.get("template_language"),
        "business_name": business.get("name"),
        "business_email_present": bool(business.get("email")),
        "business_address_present": bool(business.get("address")),
        "client_name": client.get("name"),
        "client_email_present": bool(client.get("email")),
        "client_address_present": bool(client.get("address")),
        "item_count": len(items),
        "priced_item_count": sum(1 for item in items if _mapping_or_empty(item).get("unit_price") is not None),
        "items": [_summarize_invoice_item(item) for item in items],
        "notes_present": bool(data.get("notes")),
        "payment_terms_present": bool(data.get("payment_terms")),
        "raw_items_present": bool(data.get("raw_items")),
    }


def summarize_created_invoice(created_invoice: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "invoice_id": created_invoice.get("id"),
        "invoice_number": created_invoice.get("invoice_number"),
        "subtotal": created_invoice.get("subtotal"),
        "total": created_invoice.get("total"),
        "currency": created_invoice.get("currency"),
        "pdf_url": created_invoice.get("pdf_url"),
    }


def summarize_response(body: Any) -> dict[str, Any]:
    if isinstance(body, BaseModel):
        data = body.model_dump(mode="json")
    elif isinstance(body, Mapping):
        data = dict(body)
    else:
        return {"response_type": type(body).__name__}

    summary: dict[str, Any] = {
        "status": data.get("status"),
    }
    for key in (
        "message",
        "missing_fields",
        "invoice_id",
        "invoice_number",
        "subtotal",
        "total",
        "currency",
        "pdf_url",
    ):
        if key in data:
            summary[key] = data[key]
    if "draft" in data:
        summary["draft"] = summarize_invoice_draft(_mapping_or_empty(data["draft"]))
    if "invoices" in data and isinstance(data["invoices"], list):
        summary["invoice_count"] = len(data["invoices"])
    return summary


def _summarize_invoice_item(item: Any) -> dict[str, Any]:
    data = _mapping_or_empty(item)
    return {
        "description": data.get("description"),
        "quantity": data.get("quantity"),
        "unit_price": data.get("unit_price"),
    }


def _sanitize(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return "[REDACTED]"

    if isinstance(value, BaseModel):
        return _sanitize(value.model_dump(mode="json"), key)
    if isinstance(value, Mapping):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize(item, key) for item in value]
    return value


def _truncate(value: str) -> str:
    if APP_LOG_MAX_FIELD_LENGTH <= 0 or len(value) <= APP_LOG_MAX_FIELD_LENGTH:
        return value
    return f"{value[:APP_LOG_MAX_FIELD_LENGTH]}... [truncated {len(value) - APP_LOG_MAX_FIELD_LENGTH} chars]"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _model_to_dict(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return {}
