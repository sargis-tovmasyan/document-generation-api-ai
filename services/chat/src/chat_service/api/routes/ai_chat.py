import json
import logging
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from chat_service.clients.document import (
    DocumentConflictError,
    DocumentInvalidError,
    DocumentUnavailableError,
    document_client,
)
from chat_service.core.telemetry import get_request_id
from chat_service.core.events import (
    include_frontend_message,
    include_response_body,
    log_event,
    summarize_created_invoice,
    summarize_invoice_draft,
    summarize_response,
)
from chat_service.schemas import (
    AiChatAnswerResponse,
    AiChatErrorResponse,
    AiChatInvoiceListResponse,
    AiChatMissingFieldsResponse,
    AiChatRequest,
    InvoiceDraft,
    InvoiceDraftCreatedResponse,
)
from chat_service.clients.llm import LlmServiceError, llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])

CHAT_DECISION_PROMPT = (
    "Classify the request and needed context. Actions: answer for greetings/questions; "
    "list_invoices to show/find invoices; create_invoice to create one; remember_memory "
    "to save stated information; recall_memory to retrieve it. Context: none for new "
    "requests, recent_chat for follow-ups/retries, saved_memory for remembered facts, "
    "both only when both are needed. Return action and context JSON only. "
    "Example: User: Try again -> {\"action\":\"answer\",\"context\":\"recent_chat\"}. "
    "Recent chat:\n__RECENT_CHAT__\nRequest: __USER_MESSAGE__ JSON:"
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


def _document_request_id() -> str:
    return get_request_id() or f"chat-{uuid4().hex}"


async def list_invoices():
    return await document_client.list_documents(request_id=_document_request_id())


async def _extract_draft_or_error(message: str) -> InvoiceDraft | JSONResponse:
    try:
        analysis = await document_client.extract_draft(
            message,
            request_id=_document_request_id(),
        )
        return analysis.draft
    except DocumentInvalidError:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "status": "ai_parse_error",
                "message": CHAT_PARSE_ERROR_MESSAGE,
            },
        )
    except DocumentUnavailableError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "llm_unavailable",
                "message": CHAT_LLM_UNAVAILABLE_MESSAGE,
            },
        )


async def complete_invoice_draft(draft: InvoiceDraft, *, idempotency_key: str):
    return await document_client.complete_draft(
        draft,
        request_id=_document_request_id(),
        idempotency_key=idempotency_key,
    )


async def _extract_invoice_draft_for_chat(message: str) -> InvoiceDraft | JSONResponse:
    log_event("ai.chat.invoice.extract.started", **include_frontend_message(message))
    draft = await _extract_draft_or_error(message)
    if (
        isinstance(draft, JSONResponse)
        and draft.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    ):
        empty_draft = InvoiceDraft()
        log_event(
            "ai.chat.invoice.extract.empty_draft_used",
            **include_frontend_message(message),
            draft=summarize_invoice_draft(empty_draft),
        )
        return empty_draft
    if isinstance(draft, InvoiceDraft):
        log_event(
            "ai.chat.invoice.extract.completed",
            **include_frontend_message(message),
            draft=summarize_invoice_draft(drmxãom¢G§²ÚîÆ­yĞ€€€€€É•ÍÁ½¹Í•}‰½‘ä€ôì‰ÍÑ…ÑÕÌˆè€‰…¥}Á…ÉÍ•}•ÉÉ½Èˆ°€‰µ•ÍÍ…”ˆè!Q}AIM}II=I}5MM°€‰¡…Ñ}¥ˆè¡…Ñ}¥‘ô4(€€€€€€€€€€€É•ÍÁ½¹Í•}‰½‘ä€ô™¥¹…±¥é•}É•ÍÁ½¹Í”¡É•ÍÁ½¹Í•}‰½‘ä°Á•ÉÍ¥ÍĞõ…±Í”¤4(€€€€€€€€€€€…ÁÁ•¹‘}¡…Ñ}µ•ÍÍ…”¡¡…Ñ}¥õ¡…Ñ}¥°É½±”ô‰…ÍÍ¥ÍÑ…¹Ğˆ°½¹Ñ•¹ĞõÉ•ÍÁ½¹Í•}‰½‘ål‰µ•ÍÍ…”‰t°µ•Ñ…‘…Ñ„õÉ•ÍÁ½¹Í•}‰½‘ä¤4(€€€€€€€€€€€å¥•±}ÍÍ•}•Ù•¹Ğ ‰™¥¹…°ˆ°É•ÍÁ½¹Í•}‰½‘ä¤4(€€€€€€€€€€€É•ÑÕÉ¸4(4(€€€€€€€‘•¥Í¥½¸€ô}Õ…É‘}¡…Ñ}‘•¥Í¥½¸¡Á…å±½…¹µ•ÍÍ…”°‘•¥Í¥½¸¤4(€€€€€€€…Ñ¥½¸€ô‘•¥Í¥½¸¹…Ñ¥½¸4(€€€€€€€¥˜Í•ÍÍ¥½¹}ÍÑ…Ñ”¹•Ğ ‰ÕÉÉ•¹Ñ}¥¹Ñ•¹Ğˆ¤€ôô€‰É•…Ñ•}¥¹Ù½¥”ˆ…¹Í•ÍÍ¥½¹}ÍÑ…Ñ”¹•Ğ ‰µ¥ÍÍ¥¹}™¥•±‘Ìˆ¤è4(€€€€€€€€€€€…Ñ¥½¸€ô€‰É•…Ñ•}¥¹Ù½¥”ˆ4(€€€€€€€•±¥˜…Ñ¥½¸¥¸ì‰É•µ•µ‰•É}µ•µ½Éäˆ°€‰É•…±±}µ•µ½Éä‰ô…¹‘•¥Í¥½¸¹½¹Ñ•áĞ¥¸ì‰É••¹Ñ}¡…Ğˆ°€‰‰½Ñ ‰ôè4(€€€€€€€€€€€…Ñ¥½¸€ô€‰…¹Íİ•Èˆ4(4(€€€€€€€¥˜…Ñ¥½¸€„ô€‰…¹Íİ•Èˆè4(€€€€€€€€€€€É•ÍÁ½¹Í”€ô…İ…¥Ğ}¡…Ğ 4(€€€€€€€€€€€€€€€ÍÑÉ•…µ}Á…å±½…°4(€€€€€€€€€€€€€€€‘•¥Í¥½¸õ‘•¥Í¥½¸°4(€€€€€€€€€€€€€€€±•…É¹}™É½µ}ÑÕÉ¸õ…±Í”°4(€€€€€€€€€€€€¤4(€€€€€€€€€€€½¹Ñ•¹Ğ€ô}©Í½¹}É•ÍÁ½¹Í•}½¹Ñ•¹Ğ¡É•ÍÁ½¹Í”¤¥˜¥Í¥¹ÍÑ…¹”¡É•ÍÁ½¹Í”°)M=9I•ÍÁ½¹Í”¤•±Í”É•ÍÁ½¹Í”4(€€€€€€€€€€€½¹Ñ•¹Ğ€ô™¥¹…±¥é•}É•ÍÁ½¹Í”¡½¹Ñ•¹Ğ¤4(€€€€€€€€€€€¥˜½¹Ñ•¹Ğ¹•Ğ ‰ÍÑ…ÑÕÌˆ¤¥¸ì‰¥¹Ù½¥•}±¥ÍĞˆ°€‰µ¥ÍÍ¥¹}™¥•±‘Ìˆ°€‰É•…Ñ•‰ôè4(€€€€€€€€€€€€€€€‘•™•ÉÉ•‘}±•…É¹¥¹œ€ôì4(€€€€€€€€€€€€€€€€€€€€‰ÕÍ•É}¥ˆèÁ…å±½…¹ÕÍ•É}¥°4(€€€€€€€€€€€€€€€€€€€€‰¡…Ñ}¥ˆè¡…Ñ}¥°4(€€€€€€€€€€€€€€€€€€€€‰Í•ÍÍ¥½¹}ÍÑ…Ñ”ˆè•Ñ}Í•ÍÍ¥½¹}ÍÑ…Ñ”¡¡…Ñ}¥¤°4(€€€€€€€€€€€€€€€€€€€€‰‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥ˆèÁ…å±½…¹‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥°4(€€€€€€€€€€€€€€€€€€€€‰±¥•¹Ñ}¥ˆèÁ…å±½…¹±¥•¹Ñ}¥°4(€€€€€€€€€€€€€€€ô4(€€€€€€€€€€€å¥•±}ÍÍ•}•Ù•¹Ğ ‰™¥¹…°ˆ°½¹Ñ•¹Ğ¤4(€€€€€€€€€€€É•ÑÕÉ¸4(4(€€€€€€€…ÁÁ•¹‘}¡…Ñ}µ•ÍÍ…”¡¡…Ñ}¥õ¡…Ñ}¥°É½±”ô‰ÕÍ•Èˆ°½¹Ñ•¹ĞõÁ…å±½…¹µ•ÍÍ…”¤4(€€€€€€€É••¹Ñ}µ•ÍÍ…•Ì€ô±¥ÍÑ}¡…Ñ}µ•ÍÍ…•Ì¡¡…Ñ}¥°±¥µ¥ĞôÄÈ¤4(€€€€€€€Í¡…É•‘}µ•µ½É¥•Ì€ô±¥ÍÑ}Í¡…É•‘}µ•µ½É¥•Ì 4(€€€€€€€€€€€ÕÍ•É}¥õÁ…å±½…¹ÕÍ•É}¥°4(€€€€€€€€€€€‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥õÁ…å±½…¹‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥°4(€€€€€€€€€€€±¥•¹Ñ}¥õÁ…å±½…¹±¥•¹Ñ}¥°4(€€€€€€€€¤4(€€€€€€€Í­¥±±}µ•µ½É¥•Ì€ô±¥ÍÑ}Í­¥±±}µ•µ½É¥•Ì 4(€€€€€€€€€€€ÕÍ•É}¥õÁ…å±½…¹ÕÍ•É}¥°4(€€€€€€€€€€€‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥õÁ…å±½…¹‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥°4(€€€€€€€€€€€±¥•¹Ñ}¥õÁ…å±½…¹±¥•¹Ñ}¥°4(€€€€€€€€¤4(4(€€€€€€€…¹Íİ•È€ô€ˆˆ4(€€€€€€€ÑÉäè4(€€€€€€€€€€€…Íå¹Œ™½È‘•±Ñ„¥¸}ÍÑÉ•…µ}…¹Íİ•É}İ¥Ñ¡}µ•µ½Éä 4(€€€€€€€€€€€€€€€µ•ÍÍ…”õÁ…å±½…¹µ•ÍÍ…”°4(€€€€€€€€€€€€€€€Í•ÍÍ¥½¹}ÍÑ…Ñ”õÍ•ÍÍ¥½¹}ÍÑ…Ñ”°4(€€€€€€€€€€€€€€€Í¡…É•‘}µ•µ½É¥•ÌõÍ¡…É•‘}µ•µ½É¥•Ì°4(€€€€€€€€€€€€€€€Í­¥±±}µ•µ½É¥•ÌõÍ­¥±±}µ•µ½É¥•Ì°4(€€€€€€€€€€€€€€€É••¹Ñ}µ•ÍÍ…•ÌõÉ••¹Ñ}µ•ÍÍ…•Ì°4(€€€€€€€€€€€€€€€Ñ¡¥¹­¥¹}•¹…‰±•õÁ…å±½…¹Ñ¡¥¹­¥¹}•¹…‰±•°4(€€€€€€€€€€€€€€€Ñ•µÁ•É…ÑÕÉ•}ÁÉ•Í•ĞõÁ…å±½…¹Ñ•µÁ•É…ÑÕÉ•}ÁÉ•Í•Ğ°4(€€€€€€€€€€€€€€€Í•±•Ñ•‘}½¹Ñ•áĞõ‘•¥Í¥½¸¹½¹Ñ•áĞ°4(€€€€€€€€€€€€¤è4(€€€€€€€€€€€€€€€…¹Íİ•È€¬ô‘•±Ñ„4(€€€€€€€€€€€€€€€å¥•±}ÍÍ•}•Ù•¹Ğ ‰Ñ½­•¸ˆ°ì‰½¹Ñ•¹Ğˆè‘•±Ñ…ô¤4(€€€€€€€•á•ÁĞ1±µM•ÉÙ¥•ÉÉ½Èè4(€€€€€€€€€€€É•ÍÁ½¹Í•}‰½‘ä€ôì‰ÍÑ…ÑÕÌˆè€‰±±µ}Õ¹…Ù…¥±…‰±”ˆ°€‰µ•ÍÍ…”ˆè!Q}115}U9Y%1	1}5MM°€‰¡…Ñ}¥ˆè¡…Ñ}¥‘ô4(€€€€€€€€€€€É•ÍÁ½¹Í•}‰½‘ä€ô™¥¹…±¥é•}É•ÍÁ½¹Í”¡É•ÍÁ½¹Í•}‰½‘ä°Á•ÉÍ¥ÍĞõ…±Í”¤4(€€€€€€€€€€€…ÁÁ•¹‘}¡…Ñ}µ•ÍÍ…”¡¡…Ñ}¥õ¡…Ñ}¥°É½±”ô‰…ÍÍ¥ÍÑ…¹Ğˆ°½¹Ñ•¹ĞõÉ•ÍÁ½¹Í•}‰½‘ål‰µ•ÍÍ…”‰t°µ•Ñ…‘…Ñ„õÉ•ÍÁ½¹Í•}‰½‘ä¤4(€€€€€€€€€€€å¥•±}ÍÍ•}•Ù•¹Ğ ‰™¥¹…°ˆ°É•ÍÁ½¹Í•}‰½‘ä¤4(€€€€€€€€€€€É•ÑÕÉ¸4(4(€€€€€€€…¹Íİ•È€ô…¹Íİ•È¹ÍÑÉ¥À ¤½È€‰!½Ü…¸$¡•±Àüˆ4(€€€€€€€É•ÍÁ½¹Í”€ôì‰ÍÑ…ÑÕÌˆè€‰…¹Íİ•Èˆ°€‰µ•ÍÍ…”ˆè…¹Íİ•È°€‰¡…Ñ}¥ˆè¡…Ñ}¥‘ô4(€€€€€€€É•ÍÁ½¹Í”€ô™¥¹…±¥é•}É•ÍÁ½¹Í”¡É•ÍÁ½¹Í”°Á•ÉÍ¥ÍĞõ…±Í”¤4(€€€€€€€…ÁÁ•¹‘}¡…Ñ}µ•ÍÍ…”¡¡…Ñ}¥õ¡…Ñ}¥°É½±”ô‰…ÍÍ¥ÍÑ…¹Ğˆ°½¹Ñ•¹Ğõ…¹Íİ•È°µ•Ñ…‘…Ñ„õÉ•ÍÁ½¹Í”¤4(€€€€€€€‘•™•ÉÉ•‘}±•…É¹¥¹œ€ôì4(€€€€€€€€€€€€‰ÕÍ•É}¥ˆèÁ…å±½…¹ÕÍ•É}¥°4(€€€€€€€€€€€€‰¡…Ñ}¥ˆè¡…Ñ}¥°4(€€€€€€€€€€€€‰Í•ÍÍ¥½¹}ÍÑ…Ñ”ˆèÍ•ÍÍ¥½¹}ÍÑ…Ñ”°4(€€€€€€€€€€€€‰‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥ˆèÁ…å±½…¹‰ÕÍ¥¹•ÍÍ}ÁÉ½™¥±•}¥°4(€€€€€€€€€€€€‰±¥•¹Ñ}¥ˆèÁ…å±½…¹±¥•¹Ñ}¥°4(€€€€€€€ô4(€€€€€€€å¥•±}ÍÍ•}•Ù•¹Ğ ‰™¥¹…°ˆ°É•ÍÁ½¹Í”¤4(4(€€€…Íå¹Œ‘•˜•Ù•¹ÑÌ ¤€´øÍå¹%Ñ•É…Ñ½ÉmÍÑÉtè4(€€€€€€€Ñ½­•¸€ôÍ•Ñ}±±µ}É•ÅÕ•ÍÑ}µ•ÑÉ¥Ì¡µ•ÑÉ¥Ì¤4(€€€€€€€ÑÉäè4(€€€€€€€€€€€…Íå¹Œ™½È•Ù•¹Ğ¥¸É•ÍÁ½¹Í•}•Ù•¹ÑÌ ¤è4(€€€€€€€€€€€€€€€å¥•±•Ù•¹Ğ4(€€€€€€€™¥¹…±±äè4(€€€€€€€€€€€É•Í•Ñ}±±µ}É•ÅÕ•ÍÑ}µ•ÑÉ¥Ì¡Ñ½­•¸¤4(4(€€€É•ÑÕÉ¸MÑÉ•…µ¥¹I•ÍÁ½¹Í” 4(€€€€€€€•Ù•¹ÑÌ ¤°4(€€€€€€€µ•‘¥…}ÑåÁ”ô‰Ñ•áĞ½•Ù•¹ĞµÍÑÉ•…´ˆ°4(€€€€€€€‰…­É½Õ¹õ	…­É½Õ¹‘Q…Í¬¡ÉÕ¹}‘•™•ÉÉ•‘}±•…É¹¥¹œ¤°4(€€€€¤4