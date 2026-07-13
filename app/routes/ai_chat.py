import json
import logging
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.observability_events import (
    include_frontend_message,
    include_response_body,
    log_event,
    summarize_created_invoice,
    summarize_invoice_draft,
    summarize_response,
)
from app.routes.ai_invoice import _extract_draft_or_error
from app.schemas import (
    AiChatAnswerResponse,
    AiChatErrorResponse,
    AiChatInvoiceListResponse,
    AiChatMissingFieldsResponse,
    AiChatRequest,
    InvoiceDraft,
    InvoiceDraftCreatedResponse,
)
from app.services.invoice_draft_validator import (
    find_missing_invoice_fields,
    invoice_draft_to_create,
)
from app.services.invoice_service import (
    InvoiceNumberConflictError,
    create_invoice,
    list_invoices,
)
from app.services.llm_client import LlmServiceError, llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])

CHAT_DECISION_PROMPT = (
    "Classify the user request as one action and choose the context needed to answer it. "
    "Use answer for greetings and professional questions. "
    "Use list_invoices when the user asks to show, list, find, or summarize invoices. "
    "Use create_invoice only when the user clearly wants to create an invoice. "
    "Use remember_memory when the user asks you to remember or memorize information. "
    "Use recall_memory when the user asks about information they previously asked you to remember. "
    "Use context none for new requests, recent_chat for follow-ups about this chat, "
    "saved_memory for explicitly remembered facts, and both only when both are needed. "
    "Examples: "
    "User: Hi JSON: {\"action\":\"answer\"}. "
    "User: Hello JSON: {\"action\":\"answer\"}. "
    "User: What payment terms should I use? JSON: {\"action\":\"answer\"}. "
    "User: Show me all my invoices JSON: {\"action\":\"list_invoices\"}. "
    "User: Create an invoice for Alex for design 300 dollars JSON: {\"action\":\"create_invoice\"}. "
    "User: Remember number 1234 JSON: {\"action\":\"remember_memory\"}. "
    "User: What number did I ask you to remember? JSON: {\"action\":\"recall_memory\"}. "
    "User: Try again JSON: {\"action\":\"answer\",\"context\":\"recent_chat\"}. "
    "Recent chat:\n__RECENT_CHAT__\n"
    "User: __USER_MESSAGE__ JSON:"
)
CHAT_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["answer", "list_invoices", "create_invoice", "remember_memory", "recall_memory"],
        },
        "context": {
            "type": "string",
            "enum": ["none", "recent_chat", "saved_memory", "both"],
        },
    },
    "required": ["action", "context"],
    "additionalProperties": False,
}

CHAT_LLM_UNAVAILABLE_MESSAGE = (
    "AI assistant is temporarily unavailable. Please try again later."
)
CHAT_PARSE_ERROR_MESSAGE = (
    "I could not decide how to handle that request. Please try rephrasing it."
)
INVOICE_REQUEST_PATTERN = re.compile(
    r"\b(?:invoice|invoices|bill|billing|receipt|document|documents)\b",
    re.IGNORECASE,
)
ANSWER_META_TAIL_PATTERN = re.compile(
    r"\s+(?:thought\s*:|thinking\s*:|reasoning?\s*:|reason(?:ing)?\b|confidence\s*:|the only current message is|the assistant thought|the answer\b|answer\s*:|end of conversation\b).*",
    re.IGNORECASE | re.DOTALL,
)
ROLE_ECHO_TAIL_PATTERN = re.compile(r"\s+(?:user|assistant)\s*:.*", re.IGNORECASE | re.DOTALL)
THINK_CLOSE_PATTERN = re.compile(r"</think>", re.IGNORECASE)
THINK_BLOCK_PATTERN = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
THINK_TAG_PATTERN = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
STRAY_TAG_PATTERN = re.compile(
    r"</?(?:ing|analysis|reasoning|thought|answer|assistant|user)\b[^>]*>",
    re.IGNORECASE,
)
MARKDOWN_BLOCK_PATTERN = re.compile(r"(?:^|\n)(?:```|~~~|\s*(?:[-*+] |\d+[.)] |> |\|))")


class ChatDecision(BaseModel):
    action: Literal["answer", "list_invoices", "create_invoice", "remember_memory", "recall_memory"]
    context: Literal["none", "recent_chat", "saved_memory", "both"] = "none"
    message: str = ""


TEMPERATURE_PRESETS: dict[str, float] = {
    "low": 0.2,
    "medium": 0.4,
    "high": 0.7,
    "extra_high": 1.0,
}


def _temperature_for_preset(preset: str) -> float:
    return TEMPERATURE_PRESETS.get(preset, TEMPERATURE_PRESETS["medium"])


def _load_chat_decision(content: str) -> ChatDecision:
    normalized = content.strip().lower()
    if normalized in {"answer", "list_invoices", "create_invoice", "remember_memory", "recall_memory"}:
        return ChatDecision(action=normalized, message="")

    try:
        raw_decision = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return _load_chat_decision_from_text(content)
        raw_decision = json.loads(content[start : end + 1])

    return ChatDecision.model_validate(raw_decision)


async def _decide_chat_action(
    message: str,
    recent_messages: list[dict[str, object]] | None = None,
) -> ChatDecision:
    log_event("ai.chat.decision.started", **include_frontend_message(message))
    recent_chat = "\n".join(
        f"{item.get('role', '')}: {item.get('content', '')}"
        for item in (recent_messages or [])[-2:]
    ) or "None"
    prompt = (
        CHAT_DECISION_PROMPT.replace("__RECENT_CHAT__", recent_chat)
        .replace("__USER_MESSAGE__", message)
    )
    content = await llm_client.complete_prompt(
        prompt,
        json_schema=CHAT_DECISION_SCHEMA,
        max_tokens=32,
    )
    decision = _load_chat_decision(content)
    log_event(
        "ai.chat.decision.completed",
        action=decision.action,
        decision_message=decision.message,
        **include_frontend_message(message),
    )
    return decision


def _guard_chat_decision(message: str, decision: ChatDecision) -> ChatDecision:
    if decision.action in {"answer", "remember_memory", "recall_memory"}:
        return decision
    if INVOICE_REQUEST_PATTERN.search(message):
        return decision
    return ChatDecision(action="answer", context=decision.context, message="")


def _load_chat_decision_from_text(content: str) -> ChatDecision:
    normalized = content.lower()
    for action in ("remember_memory", "recall_memory", "create_invoice", "list_invoices", "answer"):
        if action in normalized:
            return ChatDecision(action=action, message="")
    raise ValueError("LLM returned an unknown chat action")


def _thinking_instruction(thinking_enabled: bool) -> str:
    if thinking_enabled:
        return (
            "You may reason internally before answering, but return only the final "
            "user-visible answer. Do not include <think> tags, reasoning, analysis, "
            "confidence, internal notes, Thought labels, or Answer labels. "
        )
    return (
        "Do not use or reveal reasoning. Return only the final user-visible answer. "
        "Do not include <think> tags, analysis, confidence, internal notes, Thought labels, or Answer labels. "
    )


async def _answer_chat_message(
    message: str,
    thinking_enabled: bool = False,
    temperature_preset: str = "medium",
) -> str:
    log_event("ai.chat.answer.started", **include_frontend_message(message))
    prompt = (
        "You are a warm, friendly, professional document assistant. "
        f"{_thinking_instruction(thinking_enabled)}"
        "Answer the user directly in one or two short sentences. "
        "Finish with a complete sentence. "
        "For greetings, greet back and ask how you can help. Do not repeat yourself.\n"
        f"User: {message}\n"
        "Assistant:"
    )
    answer = await llm_client.complete_prompt(
        prompt,
        max_tokens=128,
        stop=["User:", "\nUser:", "\nAssistant:"],
        temperature=_temperature_for_preset(temperature_preset),
    )
    answer = _clean_chat_answer(answer)
    log_event("ai.chat.answer.completed", answer_length=len(answer))
    return answer


def _clean_chat_answer(answer: str) -> str:
    normalized = answer.strip()
    normalized = THINK_BLOCK_PATTERN.sub("", normalized).strip()
    if THINK_CLOSE_PATTERN.search(normalized):
        before, after = THINK_CLOSE_PATTERN.split(normalized, maxsplit=1)
        normalized = after.strip() or before.strip()
    normalized = THINK_TAG_PATTERN.sub("", normalized).strip()
    normalized = STRAY_TAG_PATTERN.sub("", normalized).strip()
    normalized = ANSWER_META_TAIL_PATTERN.sub("", normalized).strip()
    normalized = ROLE_ECHO_TAIL_PATTERN.sub("", normalized).strip()
    normalized = _trim_incomplete_tail(normalized)
    return _remove_repeated_answer(normalized)


def _trim_incomplete_tail(answer: str) -> str:
    normalized = answer.strip()
    if MARKDOWN_BLOCK_PATTERN.search(normalized):
        return normalized
    last_open = normalized.rfind("(")
    last_close = normalized.rfind(")")
    if last_open > last_close:
        normalized = normalized[:last_open].strip()
    if not normalized or normalized.endswith((".", "!", "?")):
        return normalized

    last_sentence_end = max(normalized.rfind("."), normalized.rfind("!"), normalized.rfind("?"))
    if last_sentence_end == -1:
        return normalized
    return normalized[: last_sentence_end + 1].strip()


def _remove_repeated_answer(answer: str) -> str:
    normalized = answer.strip()
    if not normalized:
        return normalized

    midpoint = len(normalized) // 2
    left = normalized[:midpoint].strip()
    right = normalized[midpoint:].strip()
    if len(normalized) % 2 == 0 and left == right:
        return left

    sentences = re.findall(r"[^.!?]+[.!?]+(?:\s|$)", normalized)
    if not sentences:
        return normalized
    collapsed_sentences: list[str] = []
    for sentence in sentences:
        stripped = sentence.strip()
        if not collapsed_sentences or collapsed_sentences[-1] != stripped:
            collapsed_sentences.append(stripped)
    if len(collapsed_sentences) < len(sentences):
        return " ".join(collapsed_sentences)
    if len(sentences) % 2 != 0:
        return normalized

    half = len(sentences) // 2
    first_half = [sentence.strip() for sentence in sentences[:half]]
    second_half = [sentence.strip() for sentence in sentences[half:]]
    if first_half == second_half:
        return " ".join(first_half)

    return normalized


def _invoice_list_message(invoice_count: int) -> str:
    if invoice_count == 0:
        return "You do not have any invoices yet."
    if invoice_count == 1:
        return "I found 1 invoice."
    return f"I found {invoice_count} invoices."


async def _extract_invoice_draft_for_chat(message: str) -> InvoiceDraft | JSONResponse:
    log_event("ai.chat.invoice.extract.started", **include_frontend_message(message))
    draft = await _extract_draft_or_error(message)
    if (
        isinstance(draft, JSONResponse)
        and draft.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    ):
        fallback = _fallback_invoice_draft(message)
        log_event(
            "ai.chat.invoice.extract.fallback_used",
            **include_frontend_message(message),
            draft=summarize_invoice_draft(fallback),
        )
        return fallback
    if isinstance(draft, InvoiceDraft):
        if not _has_item_amount(message):
            data = draft.model_dump()
            data["items"] = [
                item.model_dump()
                for item in draft.items
                if item.unit_price is not None
            ]
            draft = InvoiceDraft.model_validate(data)
        merged_draft = _merge_fallback_invoice_draft(draft, _fallback_invoice_draft(message))
        log_event(
            "ai.chat.invoice.extract.completed",
            **include_frontend_message(message),
            draft=summarize_invoice_draft(merged_draft),
        )
        return merged_draft
    return draft


def _fallback_invoice_draft(message: str) -> InvoiceDraft:
    return InvoiceDraft.model_validate(
        {
            "invoice_number": _extract_invoice_number(message),
            "currency": _extract_currency(message),
            "business": {"name": _extract_business_name(message)},
            "client": {"name": _extract_client_name(message)},
            "items": _extract_items(message),
        }
    )


def _merge_fallback_invoice_draft(draft: InvoiceDraft, fallback: InvoiceDraft) -> InvoiceDraft:
    data = draft.model_dump()
    if data["invoice_number"] is None:
        data["invoice_number"] = fallback.invoice_number
    if data["currency"] is None:
        data["currency"] = fallback.currency
    if data["business"]["name"] is None:
        data["business"]["name"] = fallback.business.name
    if data["client"]["name"] is None:
        data["client"]["name"] = fallback.client.name
    if not data["items"] and fallback.items:
        data["items"] = [item.model_dump() for item in fallback.items]
    return InvoiceDraft.model_validate(data)


def _has_item_amount(message: str) -> bool:
    without_invoice_number = re.sub(r"\b[A-Z]{2,}-\d+\b", "", message, flags=re.IGNORECASE)
    return bool(re.search(r"\d+(?:[.,]\d+)?\s*(?:dollars?|usd|amd|eur|rub|rur|\$|€|֏|₽)\b", without_invoice_number, flags=re.IGNORECASE))


def _extract_invoice_number(message: str) -> str | None:
    match = re.search(r"\b([A-Z]{2,}-\d+)\b", message, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _extract_client_name(message: str) -> str | None:
    labeled_match = re.search(
        r"\bclient(?:\s+name)?\s*(?:is|:)\s*(.+?)(?=\s*,?\s*(?:my\s+)?business\b|[,;]|$)",
        message,
        flags=re.IGNORECASE,
    )
    if labeled_match is not None:
        name = labeled_match.group(1).strip()
        return name if len(name.split()) <= 6 else None

    match = re.search(
        r"\b(?:for|to)\s+(?:my\s+)?(?:client\s+)?(.+?)(?=\s+(?:for|from|about|with)\b|[,.;]|$)",
        message,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    name = match.group(1).strip()
    return name if len(name.split()) <= 6 else None


def _extract_business_name(message: str) -> str | None:
    match = re.search(
        r"\b(?:my\s+)?business(?:\s+name)?\s*(?:is|:)\s*(.+?)(?=[,;]|$)",
        message,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    name = match.group(1).strip()
    return name if len(name.split()) <= 8 else None


def _extract_currency(message: str) -> str | None:
    lowered = message.lower()
    if "amd" in lowered or "dram" in lowered or "֏" in message:
        return "AMD"
    if "eur" in lowered or "euro" in lowered or "€" in message:
        return "EUR"
    if "rub" in lowered or "rur" in lowered or "ruble" in lowered or "₽" in message:
        return "RUR"
    if "usd" in lowered or "dollar" in lowered or "$" in message:
        return "USD"
    return None


def _extract_items(message: str) -> list[dict]:
    items: list[dict] = []
    for match in re.finditer(
        r"\bfor\s+(.+?)\s+(\d+(?:[.,]\d+)?)\s*(?:dollars?|usd|amd|eur|rub|rur|\$|€|֏|₽)\b",
        message,
        flags=re.IGNORECASE,
    ):
        description = match.group(1).split(" for ")[-1].strip(" ,.;")
        items.append(
            {
                "description": description,
                "quantity": 1,
                "unit_price": match.group(2).replace(",", "."),
            }
        )
    return items


@router.post(
    "",
    response_model=(
        AiChatAnswerResponse
        | AiChatInvoiceListResponse
        | AiChatMissingFieldsResponse
        | InvoiceDraftCreatedResponse
    ),
    responses={
        422: {"model": AiChatErrorResponse},
        503: {"model": AiChatErrorResponse},
    },
)
async def chat(
    payload: AiChatRequest,
) -> (
    AiChatAnswerResponse
    | AiChatInvoiceListResponse
    | AiChatMissingFieldsResponse
    | InvoiceDraftCreatedResponse
    | JSONResponse
):
    log_event("ai.chat.received", **include_frontend_message(payload.message))
    try:
        decision = await _decide_chat_action(payload.message)
    except LlmServiceError as error:
        response_body = {
            "status": "llm_unavailable",
            "message": CHAT_LLM_UNAVAILABLE_MESSAGE,
        }
        log_event(
            "ai.chat.response.sent",
            level=logging.WARNING,
            status=response_body["status"],
            error_type=type(error).__name__,
            error=str(error),
            **include_response_body(response_body),
        )
        return JSONResponse(status_code=503, content=response_body)
    except (ValueError, ValidationError, json.JSONDecodeError) as error:
        response_body = {
            "status": "ai_parse_error",
            "message": CHAT_PARSE_ERROR_MESSAGE,
        }
        log_event(
            "ai.chat.response.sent",
            level=logging.WARNING,
            status=response_body["status"],
            error_type=type(error).__name__,
            error=str(error),
            **include_response_body(response_body),
        )
        return JSONResponse(status_code=422, content=response_body)

    decision = _guard_chat_decision(payload.message, decision)

    if decision.action in {"answer", "remember_memory", "recall_memory"}:
        try:
            answer = await _answer_chat_message(
                payload.message,
                thinking_enabled=payload.thinking_enabled,
                temperature_preset=payload.temperature_preset,
            )
        except LlmServiceError as error:
            response_body = {
                "status": "llm_unavailable",
                "message": CHAT_LLM_UNAVAILABLE_MESSAGE,
            }
            log_event(
                "ai.chat.response.sent",
                level=logging.WARNING,
                status=response_body["status"],
                action=decision.action,
                error_type=type(error).__name__,
                error=str(error),
                **include_response_body(response_body),
            )
            return JSONResponse(status_code=503, content=response_body)
        response = AiChatAnswerResponse(status="answer", message=answer)
        log_event(
            "ai.chat.response.sent",
            status=response.status,
            action=decision.action,
            answer_length=len(answer),
            **include_response_body(summarize_response(response)),
        )
        return response

    if decision.action == "list_invoices":
        invoices = list_invoices()
        response = AiChatInvoiceListResponse(
            status="invoice_list",
            message=_invoice_list_message(len(invoices)),
            invoices=invoices,
        )
        log_event(
            "ai.chat.response.sent",
            status=response.status,
            action=decision.action,
            invoice_count=len(invoices),
            **include_response_body(summarize_response(response)),
        )
        return response

    draft = await _extract_invoice_draft_for_chat(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    missing_fields = find_missing_invoice_fields(draft)
    log_event(
        "ai.chat.invoice.validation.completed",
        missing_fields=missing_fields,
        draft=summarize_invoice_draft(draft),
    )
    if missing_fields:
        response = AiChatMissingFieldsResponse(
            status="missing_fields",
            missing_fields=missing_fields,
            draft=draft,
        )
        log_event(
            "ai.chat.response.sent",
            level=logging.WARNING,
            status=response.status,
            action=decision.action,
            missing_fields=missing_fields,
            draft=summarize_invoice_draft(draft),
            **include_response_body(summarize_response(response)),
        )
        return response

    invoice = invoice_draft_to_create(draft)
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        log_event(
            "ai.chat.invoice.conflict",
            level=logging.WARNING,
            invoice_number=invoice.invoice_number,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    response = InvoiceDraftCreatedResponse(
        status="created",
        invoice_id=created_invoice["id"],
        invoice_number=created_invoice["invoice_number"],
        subtotal=created_invoice["subtotal"],
        total=created_invoice["total"],
        currency=created_invoice["currency"],
        pdf_url=f"/invoices/{created_invoice['id']}/download",
    )
    log_event(
        "ai.chat.response.sent",
        status=response.status,
        action=decision.action,
        created_invoice=summarize_created_invoice(created_invoice),
        **include_response_body(summarize_response(response)),
    )
    return response
