from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.routes.ai_chat import (
    CHAT_LLM_UNAVAILABLE_MESSAGE,
    CHAT_PARSE_ERROR_MESSAGE,
    _answer_chat_message,
    _decide_chat_action,
    _extract_invoice_draft_for_chat,
    _invoice_list_message,
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
from app.services.learning_extractor import extract_and_store_learning
from app.services.llm_client import LlmServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])


class AiChatMemoryRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    chat_id: str | None = Field(default=None, max_length=100)
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=100)
    tenant_id: str | None = Field(default=None, max_length=100)
    business_profile_id: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, max_length=100)


def _draft_to_state(draft: InvoiceDraft, missing_fields: list[str], intent: str) -> dict[str, Any]:
    return {
        "active_document_type": "invoice",
        "current_intent": intent,
        "draft": draft.model_dump(mode="json"),
        "missing_fields": missing_fields,
    }


async def _learn_from_turn(user_id: str, chat_id: str, session_state: dict[str, Any]) -> None:
    try:
        await extract_and_store_learning(
            user_id=user_id,
            chat_id=chat_id,
            recent_messages=list_chat_messages(chat_id, limit=12),
            session_state=session_state,
        )
    except Exception as error:
        logger.warning("learning pass failed: %s", error)


@router.post("")
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

    if decision.action == "answer":
        try:
            answer = await _answer_chat_message(payload.message)
        except LlmServiceError:
            response_body = {"status": "llm_unavailable", "message": CHAT_LLM_UNAVAILABLE_MESSAGE, "chat_id": chat_id}
            append_chat_message(chat_id=chat_id, role="assistant", content=response_body["message"], metadata=response_body)
            return JSONResponse(status_code=503, content=response_body)
        response = {"status": "answer", "message": answer, "chat_id": chat_id}
        append_chat_message(chat_id=chat_id, role="assistant", content=answer, metadata=response)
        await _learn_from_turn(payload.user_id, chat_id, session_state)
        return response

    if decision.action == "list_invoices":
        invoices = list_invoices()
        response = {
            "status": "invoice_list",
            "message": _invoice_list_message(len(invoices)),
            "invoices": invoices,
            "chat_id": chat_id,
        }
        append_chat_message(chat_id=chat_id, role="assistant", content=response["message"], metadata=response)
        await _learn_from_turn(payload.user_id, chat_id, session_state)
        return response

    draft = await _extract_invoice_draft_for_chat(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    previous_draft = session_state.get("draft") if isinstance(session_state, dict) else None
    if isinstance(previous_draft, dict):
        merged = previous_draft | draft.model_dump(mode="json", exclude_none=True)
        try:
            draft = InvoiceDraft.model_validate(merged)
        except ValidationError:
            pass

    missing_fields = find_missing_invoice_fields(draft)
    if missing_fields:
        session_state = _draft_to_state(draft, missing_fields, decision.action)
        upsert_session_state(chat_id, session_state)
        response = {
            "status": "missing_fields",
            "missing_fields": missing_fields,
            "draft": draft.model_dump(mode="json"),
            "chat_id": chat_id,
        }
        append_chat_message(chat_id=chat_id, role="assistant", content="I need a few more details to complete your invoice.", metadata=response)
        await _learn_from_turn(payload.user_id, chat_id, session_state)
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
    await _learn_from_turn(payload.user_id, chat_id, session_state)
    return response
