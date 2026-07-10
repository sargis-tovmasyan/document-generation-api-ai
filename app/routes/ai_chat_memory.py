from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.routes.ai_chat import (
    CHAT_LLM_UNAVAILABLE_MESSAGE,
    CHAT_PARSE_ERROR_MESSAGE,
    _clean_chat_answer,
    _decide_chat_action,
    _extract_invoice_draft_for_chat,
    _guard_chat_decision,
    _invoice_list_message,
    _thinking_instruction,
    _temperature_for_preset,
)
from app.schemas import InvoiceDraft
from app.services.chat_schema import ensure_chat_schema
from app.services.chat_store import (
    DEFAULT_USER_ID,
    append_chat_message,
    clear_document_scope,
    ensure_chat_thread,
    get_session_state,
    list_chat_messages,
    upsert_session_state,
)
from app.services.invoice_draft_validator import find_missing_invoice_fields, invoice_draft_to_create
from app.services.invoice_service import InvoiceNumberConflictError, create_invoice, list_invoices
from app.services.knowledge_store import list_shared_memories, list_skill_memories, save_fact
from app.services.learning_extractor import extract_and_store_learning
from app.services.llm_client import LlmServiceError, llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])

MEMORY_CONTEXT_LEAK_PATTERN = re.compile(
    r"\s*\((?:memory\s+context|context|reasoning)\s*:\s*[^)]*\)\s*",
    re.IGNORECASE,
)
NUMBER_RECALL_PATTERN = re.compile(r"\b(?:number|code|pin)\b", re.IGNORECASE)
NUMBER_VALUE_PATTERN = re.compile(r"\b(?:number|code|pin)\s+(?:is\s+)?([A-Za-z0-9][A-Za-z0-9._-]*)\b", re.IGNORECASE)
EXPLICIT_REMEMBER_PATTERN = re.compile(
    r"\b(?:remember|memorize|save)\b(?:\s+that)?\s+(?P<memory>.+)",
    re.IGNORECASE | re.DOTALL,
)
IGNORED_MEMORY_VALUES = {"a", "an", "and", "for", "me", "the", "this", "that", "it"}

MEMORY_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "has_memory": {"type": "boolean"},
        "memory": {"type": "string"},
    },
    "required": ["has_memory", "memory"],
    "additionalProperties": False,
}


class MemoryExtract(BaseModel):
    has_memory: bool
    memory: str = Field(default="", max_length=1000)


class AiChatMemoryRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    chat_id: str | None = Field(default=None, max_length=100)
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=100)
    tenant_id: str | None = Field(default=None, max_length=100)
    business_profile_id: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, max_length=100)
    thinking_enabled: bool = False
    temperature_preset: str = Field(default="medium", pattern="^(low|medium|high|extra_high)$")


def _draft_to_state(draft: InvoiceDraft, missing_fields: list[str], intent: str) -> dict[str, Any]:
    return {
        "active_document_type": "invoice",
        "current_intent": intent,
        "draft": draft.model_dump(mode="json"),
        "missing_fields": missing_fields,
    }


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_memory_extract(content: str) -> MemoryExtract:
    try:
        return MemoryExtract.model_validate_json(content)
    except ValueError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise
        return MemoryExtract.model_validate_json(content[start : end + 1])


async def _extract_memory_to_save(message: str) -> MemoryExtract:
    prompt = (
        "Extract the concrete information the user explicitly wants the assistant to remember. "
        "If the user only asks whether you can remember something but does not provide the value, "
        "return has_memory false. Return JSON only.\n\n"
        "Examples:\n"
        "User: can you memorize a number and remind me later?\n"
        "{\"has_memory\":false,\"memory\":\"\"}\n"
        "User: remember number 1234\n"
        "{\"has_memory\":true,\"memory\":\"number 1234\"}\n"
        "User: remember that my preferred currency is AMD\n"
        "{\"has_memory\":true,\"memory\":\"my preferred currency is AMD\"}\n\n"
        f"User: {message}\n"
        "JSON:"
    )
    content = await llm_client.complete_prompt(
        prompt,
        json_schema=MEMORY_EXTRACT_SCHEMA,
        max_tokens=128,
        temperature=0.2,
    )
    return _load_memory_extract(content)


def _format_saved_memory(memory: str) -> str:
    normalized = memory.strip().rstrip(".")
    if not normalized:
        return ""
    return f"User asked me to remember {normalized}."


def _save_requested_memory(
    *,
    user_id: str,
    chat_id: str,
    memory: str,
    business_profile_id: str | None,
    client_id: str | None,
) -> None:
    saved_content = _format_saved_memory(memory)
    if not saved_content:
        return
    save_fact(
        user_id=user_id,
        source_chat_id=chat_id,
        fact_type="user_requested_memory",
        content=saved_content,
        structured={"source": "explicit_remember_request"},
        confidence=0.95,
        business_profile_id=business_profile_id,
        client_id=client_id,
    )


def _pending_memory_value_from_message(message: str, session_state: dict[str, Any]) -> str:
    pending = session_state.get("pending_memory_request")
    if not isinstance(pending, dict):
        return ""
    if pending.get("kind") == "number":
        match = NUMBER_VALUE_PATTERN.search(message.strip())
        if match:
            value = match.group(1).strip(" .!?")
            if value.lower() not in IGNORED_MEMORY_VALUES:
                return f"number {value}"
    return ""


def _fallback_memory_from_message(message: str) -> str:
    match = EXPLICIT_REMEMBER_PATTERN.search(message.strip())
    if not match:
        return ""
    memory = match.group("memory").strip(" .!?")
    if not memory or memory.lower().startswith(("a ", "an ", "the ")):
        return ""
    if NUMBER_RECALL_PATTERN.search(memory):
        value = _number_memory_value(memory)
        return f"number {value}" if value else ""
    return memory


def _number_memory_value(memory: str) -> str:
    match = NUMBER_VALUE_PATTERN.search(memory.strip())
    if not match:
        return ""
    value = match.group(1).strip(" .!?")
    return "" if value.lower() in IGNORED_MEMORY_VALUES else value


def _has_concrete_memory(message: str, memory: str) -> bool:
    normalized = memory.strip()
    if not normalized:
        return False
    if NUMBER_RECALL_PATTERN.search(message):
        return bool(_number_memory_value(normalized))
    return True


def _remembered_number_from_memories(shared_memories: list[dict[str, Any]], chat_id: str) -> str | None:
    sorted_memories = sorted(
        shared_memories,
        key=lambda memory: 0 if memory.get("source_chat_id") == chat_id else 1,
    )
    for memory in sorted_memories:
        content = str(memory.get("content", ""))
        for match in re.finditer(r"\b(?:number|code|pin)\s+([A-Za-z0-9][A-Za-z0-9._-]*)\b", content, re.IGNORECASE):
            value = match.group(1).strip(" .!?")
            if value.lower() not in IGNORED_MEMORY_VALUES:
                return value
    return None


def _recall_memory_answer(message: str, shared_memories: list[dict[str, Any]], chat_id: str) -> str:
    if not shared_memories:
        return "I do not have anything saved for that yet."

    if NUMBER_RECALL_PATTERN.search(message):
        remembered_number = _remembered_number_from_memories(shared_memories, chat_id)
        if remembered_number:
            return f"The number you asked me to remember is {remembered_number}."

    latest = str(shared_memories[0].get("content", "")).strip()
    if latest.lower().startswith("user asked me to remember "):
        latest = latest[len("User asked me to remember ") :].strip()
    return f"You asked me to remember {latest}"


async def _learn_from_turn(
    *,
    user_id: str,
    chat_id: str,
    session_state: dict[str, Any],
    business_profile_id: str | None,
    client_id: str | None,
) -> None:
    try:
        await extract_and_store_learning(
            user_id=user_id,
            chat_id=chat_id,
            recent_messages=list_chat_messages(chat_id, limit=12),
            session_state=session_state,
            business_profile_id=business_profile_id,
            client_id=client_id,
        )
    except Exception as error:
        logger.warning("learning pass failed: %s", error)


def _format_context_section(
    *,
    session_state: dict[str, Any],
    shared_memories: list[dict[str, Any]],
    skill_memories: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    if session_state:
        lines.append(f"Session memory JSON: {session_state}")
    if shared_memories:
        lines.append("Relevant shared memories:")
        lines.extend(f"- {memory['content']}" for memory in shared_memories)
    if skill_memories:
        lines.append("Relevant skill memories:")
        lines.extend(f"- {skill['title']}: {skill['description']}" for skill in skill_memories)
    if recent_messages:
        lines.append("Recent messages:")
        lines.extend(
            f"{message['role']}: {message['content']}"
            for message in recent_messages[-8:]
        )
    return "\n".join(lines)


async def _answer_chat_message_with_memory(
    *,
    message: str,
    session_state: dict[str, Any],
    shared_memories: list[dict[str, Any]],
    skill_memories: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
    thinking_enabled: bool = False,
    temperature_preset: str = "medium",
) -> str:
    context = _format_context_section(
        session_state=session_state,
        shared_memories=shared_memories,
        skill_memories=skill_memories,
        recent_messages=recent_messages,
    )
    prompt = (
        "You are a warm, friendly, professional document assistant. "
        f"{_thinking_instruction(thinking_enabled)}"
        "Answer the current user message in one or two short sentences. "
        "Finish with a complete sentence. "
        "Use the provided memory context when relevant. "
        "You have access to saved memories and recent messages. "
        "Do not expose raw memory, context, prompts, or reasoning text. "
        "Do not claim you have no memory when saved memories or recent messages are provided. "
        "Do not repeat yourself.\n\n"
        f"{context}\n\n"
        f"Current user message: {message}\n"
        "Assistant:"
    )
    answer = await llm_client.complete_prompt(
        prompt,
        max_tokens=128,
        stop=["User:", "\nUser:", "\nAssistant:"],
        temperature=_temperature_for_preset(temperature_preset),
    )
    answer = MEMORY_CONTEXT_LEAK_PATTERN.sub(" ", answer)
    return MEMORY_CONTEXT_LEAK_PATTERN.sub(" ", _clean_chat_answer(answer)).strip()


@router.post("", response_model=None)
async def chat(payload: AiChatMemoryRequest) -> dict[str, Any] | JSONResponse:
    ensure_chat_schema()
    thread = ensure_chat_thread(
        chat_id=payload.chat_id,
        user_id=payload.user_id,
        business_profile_id=payload.business_profile_id,
        client_id=payload.client_id,
        title=payload.message[:80],
    )
    chat_id = thread["id"]
    append_chat_message(chat_id=chat_id, role="user", content=payload.message)
    session_state = get_session_state(chat_id)
    recent_messages = list_chat_messages(chat_id, limit=12)
    shared_memories = list_shared_memories(
        user_id=payload.user_id,
        business_profile_id=payload.business_profile_id,
        client_id=payload.client_id,
    )
    skill_memories = list_skill_memories(
        user_id=payload.user_id,
        business_profile_id=payload.business_profile_id,
        client_id=payload.client_id,
    )

    pending_memory = _pending_memory_value_from_message(payload.message, session_state)
    if pending_memory:
        _save_requested_memory(
            user_id=payload.user_id,
            chat_id=chat_id,
            memory=pending_memory,
            business_profile_id=payload.business_profile_id,
            client_id=payload.client_id,
        )
        session_state.pop("pending_memory_request", None)
        upsert_session_state(chat_id, session_state)
        answer = "Got it. I will remember that."
        response = {"status": "answer", "message": answer, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=answer, metadata=response)
        return response

    try:
        decision = await _decide_chat_action(payload.message)
    except LlmServiceError:
        response_body = {"status": "llm_unavailable", "message": CHAT_LLM_UNAVAILABLE_MESSAGE, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
        return JSONResponse(status_code=503, content=response_body)
    except (ValueError, ValidationError):
        response_body = {"status": "ai_parse_error", "message": CHAT_PARSE_ERROR_MESSAGE, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
        return JSONResponse(status_code=422, content=response_body)

    decision = _guard_chat_decision(payload.message, decision)
    action = decision.action
    if session_state.get("current_intent") == "create_invoice" and session_state.get("missing_fields"):
        action = "create_invoice"

    if action == "remember_memory":
        try:
            memory_extract = await _extract_memory_to_save(payload.message)
        except LlmServiceError:
            response_body = {"status": "llm_unavailable", "message": CHAT_LLM_UNAVAILABLE_MESSAGE, "chat_id": chat_id}
            append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
            return JSONResponse(status_code=503, content=response_body)
        except (ValueError, ValidationError):
            memory_extract = MemoryExtract(has_memory=False, memory="")

        if not memory_extract.has_memory or not memory_extract.memory.strip():
            fallback_memory = _fallback_memory_from_message(payload.message)
            if fallback_memory:
                memory_extract = MemoryExtract(has_memory=True, memory=fallback_memory)

        if memory_extract.has_memory and not _has_concrete_memory(payload.message, memory_extract.memory):
            memory_extract = MemoryExtract(has_memory=False, memory="")

        if not memory_extract.has_memory or not memory_extract.memory.strip():
            answer = "Yes, send me the number and I will remember it for this chat."
            if NUMBER_RECALL_PATTERN.search(payload.message):
                session_state["pending_memory_request"] = {"kind": "number"}
                upsert_session_state(chat_id, session_state)
        else:
            saved_content = _format_saved_memory(memory_extract.memory)
            if saved_content:
                _save_requested_memory(
                    user_id=payload.user_id,
                    chat_id=chat_id,
                    memory=memory_extract.memory,
                    business_profile_id=payload.business_profile_id,
                    client_id=payload.client_id,
                )
                answer = "Got it. I will remember that."
            else:
                answer = "Yes, send me what you want me to remember."

        response = {"status": "answer", "message": answer, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=answer, metadata=response)
        return response

    if action == "recall_memory":
        shared_memories = list_shared_memories(
            user_id=payload.user_id,
            business_profile_id=payload.business_profile_id,
            client_id=payload.client_id,
        )
        answer = _recall_memory_answer(payload.message, shared_memories, chat_id)
        response = {"status": "answer", "message": answer, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=answer, metadata=response)
        return response

    if action == "answer":
        try:
            answer = await _answer_chat_message_with_memory(
                message=payload.message,
                session_state=session_state,
                shared_memories=shared_memories,
                skill_memories=skill_memories,
                recent_messages=recent_messages,
                thinking_enabled=payload.thinking_enabled,
                temperature_preset=payload.temperature_preset,
            )
        except LlmServiceError:
            response_body = {"status": "llm_unavailable", "message": CHAT_LLM_UNAVAILABLE_MESSAGE, "chat_id": chat_id}
            append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
            return JSONResponse(status_code=503, content=response_body)
        response = {"status": "answer", "message": answer, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=answer, metadata=response)
        await _learn_from_turn(
            user_id=payload.user_id,
            chat_id=chat_id,
            session_state=session_state,
            business_profile_id=payload.business_profile_id,
            client_id=payload.client_id,
        )
        return response

    if action == "list_invoices":
        invoices = list_invoices()
        response = {
            "status": "invoice_list",
            "message": _invoice_list_message(len(invoices)),
            "invoices": invoices,
            "chat_id": chat_id,
        }
        append_chat_message(chat_id=chat_id, role="assistant", content=response["message"], metadata=response)
        await _learn_from_turn(
            user_id=payload.user_id,
            chat_id=chat_id,
            session_state=session_state,
            business_profile_id=payload.business_profile_id,
            client_id=payload.client_id,
        )
        return response

    draft = await _extract_invoice_draft_for_chat(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    previous_draft = session_state.get("draft") if isinstance(session_state, dict) else None
    if isinstance(previous_draft, dict):
        merged = _deep_merge(previous_draft, draft.model_dump(mode="json", exclude_none=True))
        try:
            draft = InvoiceDraft.model_validate(merged)
        except ValidationError:
            pass

    missing_fields = find_missing_invoice_fields(draft)
    if missing_fields:
        session_state = _draft_to_state(draft, missing_fields, action)
        upsert_session_state(chat_id, session_state)
        response = {
            "status": "missing_fields",
            "missing_fields": missing_fields,
            "draft": draft.model_dump(mode="json"),
            "chat_id": chat_id,
        }
        append_chat_message(chat_id=chat_id, role="assistant", content="I need a few more details to complete your invoice.", metadata=response)
        await _learn_from_turn(
            user_id=payload.user_id,
            chat_id=chat_id,
            session_state=session_state,
            business_profile_id=payload.business_profile_id,
            client_id=payload.client_id,
        )
        return response

    invoice = invoice_draft_to_create(draft)
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    session_state = get_session_state(chat_id)
    session_state["last_document_id"] = created_invoice["id"]
    upsert_session_state(chat_id, session_state)
    clear_document_scope(chat_id)

    response = {
        "status": "created",
        "invoice_id": created_invoice["id"],
        "invoice_number": created_invoice["invoice_number"],
        "subtotal": float(created_invoice["subtotal"]),
        "total": float(created_invoice["total"]),
        "currency": created_invoice["currency"],
        "pdf_url": f"/invoices/{created_invoice['id']}/download",
        "chat_id": chat_id,
    }
    append_chat_message(chat_id=chat_id, role="assistant", content=f"Invoice created — {created_invoice['invoice_number']}", metadata=response)
    await _learn_from_turn(
        user_id=payload.user_id,
        chat_id=chat_id,
        session_state=session_state,
        business_profile_id=payload.business_profile_id,
        client_id=payload.client_id,
    )
    return response
