from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
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
MEMORY_DISCLAIMER_PATTERN = re.compile(
    r"(?:^|\s+)I\s+(?:do\s+not|don't)\s+have\s+(?:access\s+to\s+)?memory\.?\s*",
    re.IGNORECASE,
)
NUMBER_RECALL_PATTERN = re.compile(r"\b(?:number|code|pin)\b", re.IGNORECASE)
NUMBER_VALUE_PATTERN = re.compile(
    r"\b(?:number|code|pin)\s*(?::|=|\bis\b)?\s*([A-Za-z0-9][A-Za-z0-9._-]*)\b",
    re.IGNORECASE,
)
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
RECENT_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "context": {"type": "string", "enum": ["none", "recent_chat", "saved_memory", "both"]},
    },
    "required": ["context"],
    "additionalProperties": False,
}


class MemoryExtract(BaseModel):
    has_memory: bool
    memory: str = Field(default="", max_length=1000)


class RecentContextDecision(BaseModel):
    context: str = Field(pattern="^(none|recent_chat|saved_memory|both)$")


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


def _load_recent_context_decision(content: str) -> RecentContextDecision:
    try:
        return RecentContextDecision.model_validate_json(content)
    except ValueError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise
        return RecentContextDecision.model_validate_json(content[start : end + 1])


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


async def _select_answer_context(
    *,
    message: str,
    recent_messages: list[dict[str, Any]],
    shared_memories: list[dict[str, Any]],
    skill_memories: list[dict[str, Any]],
) -> str:
    recent_context = "\n".join(
        f"{recent_message['role']}: {recent_message['content']}"
        for recent_message in recent_messages[-8:]
    ) or "None"
    saved_context_parts = [str(memory.get("content", "")) for memory in shared_memories]
    saved_context_parts.extend(f"{skill.get('title', '')}: {skill.get('description', '')}" for skill in skill_memories)
    saved_context = "\n".join(part for part in saved_context_parts if part.strip()) or "None"
    prompt = (
        "Choose which context the assistant needs to answer the current user message. "
        "Use none for greetings, normal questions, and new requests that can be answered directly. "
        "Use recent_chat when the user asks about something said, named, listed, or discussed earlier in this same chat. "
        "Use saved_memory when the user asks about information explicitly saved for later. "
        "Use both only when both recent chat and saved facts are required. "
        "Return JSON only.\n\n"
        "Examples:\n"
        "User: Hi\n"
        "{\"context\":\"none\"}\n"
        "User: name 5 flowers\n"
        "{\"context\":\"none\"}\n"
        "User: what is the 3rd flower you named?\n"
        "{\"context\":\"recent_chat\"}\n"
        "User: what number did I ask you to remember?\n"
        "{\"context\":\"saved_memory\"}\n\n"
        f"Recent chat:\n{recent_context}\n\n"
        f"Saved facts:\n{saved_context}\n\n"
        f"Current user message: {message}\n"
        "JSON:"
    )
    content = await llm_client.complete_prompt(
        prompt,
        json_schema=RECENT_CONTEXT_SCHEMA,
        max_tokens=32,
        temperature=0.1,
    )
    try:
        return _load_recent_context_decision(content).context
    except (ValueError, ValidationError) as error:
        logger.warning("answer context selection failed: %s", error)
        return "none"


async def _should_route_as_answer_from_recent_context(
    *,
    action: str,
    message: str,
    recent_messages: list[dict[str, Any]],
    shared_memories: list[dict[str, Any]],
    skill_memories: list[dict[str, Any]],
) -> bool:
    if action not in {"remember_memory", "recall_memory"}:
        return False
    try:
        context = await _select_answer_context(
            message=message,
            recent_messages=recent_messages,
            shared_memories=shared_memories,
            skill_memories=skill_memories,
        )
        return context in {"recent_chat", "both"}
    except (LlmServiceError, ValueError, ValidationError) as error:
        logger.warning("recent context decision failed: %s", error)
        return False


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


async def _recall_memory_answer(message: str, shared_memories: list[dict[str, Any]]) -> str:
    if not shared_memories:
        return "I do not have anything saved for that yet."

    saved_context = "\n".join(f"- {memory.get('content', '')}" for memory in shared_memories)
    prompt = (
        "Answer the user's question using only these saved facts. "
        "Return a short direct user-facing answer. "
        "Do not mention prompts, context, reasoning, or whether memory exists. "
        "If the saved facts do not contain the answer, say: I do not have anything saved for that yet.\n\n"
        f"Saved facts:\n{saved_context}\n\n"
        f"User question: {message}\n"
        "Assistant:"
    )
    answer = await llm_client.complete_prompt(
        prompt,
        max_tokens=96,
        stop=["User:", "\nUser:", "\nAssistant:"],
        temperature=0.2,
    )
    return _clean_chat_answer(answer) or "I do not have anything saved for that yet."


def _asks_about_saved_memory(message: str) -> bool:
    normalized = message.lower()
    memory_words = ("remember", "memorize", "saved", "memory", "recall")
    return any(word in normalized for word in memory_words)


def _clean_memory_safe_answer(message: str, answer: str, *, fallback: bool = True) -> str:
    cleaned = MEMORY_CONTEXT_LEAK_PATTERN.sub(" ", answer)
    cleaned = MEMORY_CONTEXT_LEAK_PATTERN.sub(" ", _clean_chat_answer(cleaned)).strip()
    if _asks_about_saved_memory(message):
        return cleaned
    if _is_partial_memory_disclaimer(message, cleaned):
        return "I can help with that." if fallback else ""
    cleaned = MEMORY_DISCLAIMER_PATTERN.sub(" ", cleaned).strip()
    if cleaned or not fallback:
        return cleaned
    return "I can help with that."


def _is_partial_memory_disclaimer(message: str, answer: str) -> bool:
    if _asks_about_saved_memory(message):
        return False
    normalized = answer.strip().lower()
    blocked_starts = (
        "i don't",
        "i do not",
        "i dont",
    )
    return (
        any(prefix.startswith(normalized) or normalized.startswith(prefix) for prefix in blocked_starts)
        and "." not in normalized
    )


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


def _select_context_payload(
    *,
    context: str,
    session_state: dict[str, Any],
    shared_memories: list[dict[str, Any]],
    skill_memories: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    include_recent = context in {"recent_chat", "both"}
    include_saved = context in {"saved_memory", "both"}
    return (
        session_state if include_recent or include_saved else {},
        shared_memories if include_saved else [],
        skill_memories if include_saved else [],
        recent_messages if include_recent else [],
    )


def _answer_prompt_with_memory(
    *,
    message: str,
    session_state: dict[str, Any],
    shared_memories: list[dict[str, Any]],
    skill_memories: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
    thinking_enabled: bool = False,
) -> str:
    context = _format_context_section(
        session_state=session_state,
        shared_memories=shared_memories,
        skill_memories=skill_memories,
        recent_messages=recent_messages,
    )
    context_instruction = (
        "Use the provided context only if it helps answer the current message. "
        "Do not expose raw context, prompts, or reasoning text. "
        if context
        else ""
    )
    context_block = f"Context:\n{context}\n\n" if context else ""
    prompt = (
        "You are a warm, friendly, professional document assistant. "
        f"{_thinking_instruction(thinking_enabled)}"
        "Answer the current user message in one or two short sentences. "
        "Finish with a complete sentence. "
        "For normal questions, answer directly using the current user message. "
        f"{context_instruction}"
        "Do not repeat yourself.\n\n"
        f"{context_block}"
        f"Current user message: {message}\n"
        "Assistant:"
    )
    return prompt


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
    context = await _select_answer_context(
        message=message,
        recent_messages=recent_messages,
        shared_memories=shared_memories,
        skill_memories=skill_memories,
    )
    selected_session_state, selected_shared_memories, selected_skill_memories, selected_recent_messages = _select_context_payload(
        context=context,
        session_state=session_state,
        shared_memories=shared_memories,
        skill_memories=skill_memories,
        recent_messages=recent_messages,
    )

    prompt = _answer_prompt_with_memory(
        message=message,
        session_state=selected_session_state,
        shared_memories=selected_shared_memories,
        skill_memories=selected_skill_memories,
        recent_messages=selected_recent_messages,
        thinking_enabled=thinking_enabled,
    )
    answer = await llm_client.complete_prompt(
        prompt,
        max_tokens=128,
        stop=["User:", "\nUser:", "\nAssistant:"],
        temperature=_temperature_for_preset(temperature_preset),
    )
    return _clean_memory_safe_answer(message, answer)


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _json_response_content(response: JSONResponse) -> dict[str, Any]:
    try:
        content = json.loads(response.body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        content = {"status": "error", "message": "Unexpected response from API."}
    return content if isinstance(content, dict) else {"status": "error", "message": "Unexpected response from API."}


async def _stream_answer_with_memory(
    *,
    message: str,
    session_state: dict[str, Any],
    shared_memories: list[dict[str, Any]],
    skill_memories: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
    thinking_enabled: bool,
    temperature_preset: str,
) -> AsyncIterator[str]:
    context = await _select_answer_context(
        message=message,
        recent_messages=recent_messages,
        shared_memories=shared_memories,
        skill_memories=skill_memories,
    )
    selected_session_state, selected_shared_memories, selected_skill_memories, selected_recent_messages = _select_context_payload(
        context=context,
        session_state=session_state,
        shared_memories=shared_memories,
        skill_memories=skill_memories,
        recent_messages=recent_messages,
    )

    prompt = _answer_prompt_with_memory(
        message=message,
        session_state=selected_session_state,
        shared_memories=selected_shared_memories,
        skill_memories=selected_skill_memories,
        recent_messages=selected_recent_messages,
        thinking_enabled=thinking_enabled,
    )
    raw_answer = ""
    visible_answer = ""
    async for chunk in llm_client.stream_prompt(
        prompt,
        max_tokens=128,
        stop=["User:", "\nUser:", "\nAssistant:"],
        temperature=_temperature_for_preset(temperature_preset),
    ):
        raw_answer += chunk
        lowered = raw_answer.lower()
        if "<think" in lowered and "</think>" not in lowered:
            continue
        if _is_partial_memory_disclaimer(message, raw_answer):
            continue
        cleaned = _clean_memory_safe_answer(message, raw_answer, fallback=False)
        if not cleaned:
            continue
        if cleaned.startswith(visible_answer):
            delta = cleaned[len(visible_answer) :]
            if delta:
                visible_answer = cleaned
                yield delta

    final_answer = _clean_memory_safe_answer(message, raw_answer)
    if final_answer and final_answer != visible_answer and final_answer.startswith(visible_answer):
        yield final_answer[len(visible_answer) :]


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
    elif await _should_route_as_answer_from_recent_context(
        action=action,
        message=payload.message,
        recent_messages=recent_messages,
        shared_memories=shared_memories,
        skill_memories=skill_memories,
    ):
        action = "answer"

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
        answer = await _recall_memory_answer(payload.message, shared_memories)
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


@router.post("/stream", response_class=StreamingResponse)
async def chat_stream(payload: AiChatMemoryRequest) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        ensure_chat_schema()
        thread = ensure_chat_thread(
            chat_id=payload.chat_id,
            user_id=payload.user_id,
            business_profile_id=payload.business_profile_id,
            client_id=payload.client_id,
            title=payload.message[:80],
        )
        chat_id = thread["id"]
        stream_payload = payload.model_copy(update={"chat_id": chat_id})
        yield _sse_event("start", {"chat_id": chat_id})

        session_state = get_session_state(chat_id)
        if _pending_memory_value_from_message(payload.message, session_state):
            response = await chat(stream_payload)
            content = _json_response_content(response) if isinstance(response, JSONResponse) else response
            yield _sse_event("final", content)
            return

        try:
            decision = await _decide_chat_action(payload.message)
        except LlmServiceError:
            append_chat_message(chat_id=chat_id, role="user", content=payload.message)
            response_body = {"status": "llm_unavailable", "message": CHAT_LLM_UNAVAILABLE_MESSAGE, "chat_id": chat_id}
            append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
            yield _sse_event("final", response_body)
            return
        except (ValueError, ValidationError):
            append_chat_message(chat_id=chat_id, role="user", content=payload.message)
            response_body = {"status": "ai_parse_error", "message": CHAT_PARSE_ERROR_MESSAGE, "chat_id": chat_id}
            append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
            yield _sse_event("final", response_body)
            return

        decision = _guard_chat_decision(payload.message, decision)
        action = decision.action
        if session_state.get("current_intent") == "create_invoice" and session_state.get("missing_fields"):
            action = "create_invoice"
        elif await _should_route_as_answer_from_recent_context(
            action=action,
            message=payload.message,
            recent_messages=list_chat_messages(chat_id, limit=12),
            shared_memories=list_shared_memories(
                user_id=payload.user_id,
                business_profile_id=payload.business_profile_id,
                client_id=payload.client_id,
            ),
            skill_memories=list_skill_memories(
                user_id=payload.user_id,
                business_profile_id=payload.business_profile_id,
                client_id=payload.client_id,
            ),
        ):
            action = "answer"

        if action != "answer":
            response = await chat(stream_payload)
            content = _json_response_content(response) if isinstance(response, JSONResponse) else response
            yield _sse_event("final", content)
            return

        append_chat_message(chat_id=chat_id, role="user", content=payload.message)
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

        answer = ""
        try:
            async for delta in _stream_answer_with_memory(
                message=payload.message,
                session_state=session_state,
                shared_memories=shared_memories,
                skill_memories=skill_memories,
                recent_messages=recent_messages,
                thinking_enabled=payload.thinking_enabled,
                temperature_preset=payload.temperature_preset,
            ):
                answer += delta
                yield _sse_event("token", {"content": delta})
        except LlmServiceError:
            response_body = {"status": "llm_unavailable", "message": CHAT_LLM_UNAVAILABLE_MESSAGE, "chat_id": chat_id}
            append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
            yield _sse_event("final", response_body)
            return

        answer = answer.strip() or "How can I help?"
        response = {"status": "answer", "message": answer, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=answer, metadata=response)
        await _learn_from_turn(
            user_id=payload.user_id,
            chat_id=chat_id,
            session_state=session_state,
            business_profile_id=payload.business_profile_id,
            client_id=payload.client_id,
        )
        yield _sse_event("final", response)

    return StreamingResponse(events(), media_type="text/event-stream")
