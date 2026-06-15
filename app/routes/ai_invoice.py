from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas import (
    AiInvoiceErrorResponse,
    AiInvoiceExtractRequest,
    AiInvoiceExtractResponse,
)
from app.services.ai_invoice_extractor import (
    AiInvoiceParseError,
    ai_invoice_extractor,
)
from app.services.invoice_draft_validator import find_missing_invoice_fields
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
    try:
        draft = await ai_invoice_extractor.extract(payload.message)
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

    missing_fields = find_missing_invoice_fields(draft)
    return AiInvoiceExtractResponse(
        status="missing_fields" if missing_fields else "ready",
        draft=draft,
        missing_fields=missing_fields,
    )
