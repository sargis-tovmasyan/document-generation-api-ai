from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.schemas import (
    AiInvoiceErrorResponse,
    AiInvoiceExtractRequest,
    AiInvoiceExtractResponse,
    InvoiceDraft,
    InvoiceDraftCreatedResponse,
    InvoiceDraftMissingResponse,
)
from app.services.ai_invoice_extractor import (
    AiInvoiceParseError,
    ai_invoice_extractor,
)
from app.services.invoice_draft_validator import (
    find_missing_invoice_fields,
    invoice_draft_to_create,
)
from app.services.invoice_service import InvoiceNumberConflictError, create_invoice
from app.services.llm_client import LlmServiceError

router = APIRouter(prefix="/ai/invoice", tags=["ai-invoice"])

LLM_UNAVAILABLE_MESSAGE = (
    "AI assistant is temporarily unavailable. "
    "Please enter invoice details manually."
)
AI_PARSE_ERROR_MESSAGE = (
    "Could not extract invoice details. "
    "Please provide client name, invoice items, and prices manually."
)


async def _extract_draft_or_error(message: str) -> InvoiceDraft | JSONResponse:
    try:
        return await ai_invoice_extractor.extract(message)
    except LlmServiceError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "llm_unavailable",
                "message": LLM_UNAVAILABLE_MESSAGE,
            },
        )
    except AiInvoiceParseError:
        return JSONResponse(
            status_code=422,
            content={
                "status": "ai_parse_error",
                "message": AI_PARSE_ERROR_MESSAGE,
            },
        )


@router.post(
    "/extract",
    response_model=AiInvoiceExtractResponse,
    responses={
        422: {"model": AiInvoiceErrorResponse},
        503: {"model": AiInvoiceErrorResponse},
    },
)
async def extract_invoice_draft(
    payload: AiInvoiceExtractRequest,
) -> AiInvoiceExtractResponse | JSONResponse:
    draft = await _extract_draft_or_error(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    missing_fields = find_missing_invoice_fields(draft)
    return AiInvoiceExtractResponse(
        status="missing_fields" if missing_fields else "ready",
        draft=draft,
        missing_fields=missing_fields,
    )


@router.post(
    "/generate",
    response_model=InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse,
    responses={
        409: {"description": "Invoice number already exists"},
        422: {"model": AiInvoiceErrorResponse},
        503: {"model": AiInvoiceErrorResponse},
    },
)
async def generate_invoice_from_message(
    payload: AiInvoiceExtractRequest,
) -> InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse | JSONResponse:
    draft = await _extract_draft_or_error(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    missing_fields = find_missing_invoice_fields(draft)
    if missing_fields:
        return InvoiceDraftMissingResponse(
            status="missing_fields",
            missing_fields=missing_fields,
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
