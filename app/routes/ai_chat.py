import json
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.routes.ai_invoice import _extract_draft_or_error
from app.schemas import (
    AiChatAnswerResponse,
    AiChatErrorResponse,
    AiChatInvoiceListResponse,
    AiChatMissingFieldsResponse,
    AiChatRequest,
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

router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])

CHAT_DECISION_PROMPT = """Classify the user request.
Actions: answer, list_invoices, create_invoice.

User: hi
Action: answer

User: show me all my invoices
Action: list_invoices

User: create invoice for Alex for design 300 dollars
Action: create_invoice

User: __USER_MESSAGE__
Action:
"""

CHAT_LLM_UNAVAILABLE_MESSAGE = (
    "AI assistant is temporarily unavailable. Please try again later."
)
CHAT_PARSE_ERROR_MESSAGE = (
    "I could not decide how to handle that request. Please try rephrasing it."
)


class ChatDecision(BaseModel):
    action: Literal["answer", "list_invoices", "create_invoice"]
    message: str


def _load_chat_decision(content: str) -> ChatDecision:
    normalized = content.strip().lower()
    if normalized in {"answer", "list_invoices", "create_invoice"}:
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


async def _decide_chat_action(message: str) -> ChatDecision:
    prompt = CHAT_DECISION_PROMPT.replace("__USER_MESSAGE__", message)
    content = await llm_client.complete_prompt(
        prompt,
        max_tokens=8,
        stop=["User:"],
    )
    return _load_chat_decision(content)


def _load_chat_decision_from_text(content: str) -> ChatDecision:
    normalized = content.lower()
    for action in ("create_invoice", "list_invoices", "answer"):
        if action in normalized:
            return ChatDecision(action=action, message="")
    raise ValueError("LLM returned an unknown chat action")


async def _answer_chat_message(message: str) -> str:
    prompt = (
        "You are a warm, friendly, professional document assistant. "
        "Answer the user directly and concisely.\n"
        f"User: {message}\n"
        "Assistant:"
    )
    return await llm_client.complete_prompt(prompt)


def _invoice_list_message(invoice_count: int) -> str:
    if invoice_count == 0:
        return "You do not have any invoices yet."
    if invoice_count == 1:
        return "I found 1 invoice."
    return f"I found {invoice_count} invoices."


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
    try:
        decision = await _decide_chat_action(payload.message)
    except LlmServiceError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "llm_unavailable",
                "message": CHAT_LLM_UNAVAILABLE_MESSAGE,
            },
        )
    except (ValueError, ValidationError, json.JSONDecodeError):
        return JSONResponse(
            status_code=422,
            content={
                "status": "ai_parse_error",
                "message": CHAT_PARSE_ERROR_MESSAGE,
            },
        )

    if decision.action == "answer":
        try:
            answer = await _answer_chat_message(payload.message)
        except LlmServiceError:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "llm_unavailable",
                    "message": CHAT_LLM_UNAVAILABLE_MESSAGE,
                },
            )
        return AiChatAnswerResponse(status="answer", message=answer)

    if decision.action == "list_invoices":
        invoices = list_invoices()
        return AiChatInvoiceListResponse(
            status="invoice_list",
            message=_invoice_list_message(len(invoices)),
            invoices=invoices,
        )

    draft = await _extract_draft_or_error(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    missing_fields = find_missing_invoice_fields(draft)
    if missing_fields:
        return AiChatMissingFieldsResponse(
            status="missing_fields",
            missing_fields=missing_fields,
            draft=draft,
        )

    invoice = invoice_draft_to_create(draft)
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    return InvoiceDraftCreatedResponse(
        status="created",
        invoice_id=created_invoice["id"],
        invoice_number=created_invoice["invoice_number"],
        subtotal=created_invoice["subtotal"],
        total=created_invoice["total"],
        currency=created_invoice["currency"],
        pdf_url=f"/invoices/{created_invoice['id']}/download",
    )
