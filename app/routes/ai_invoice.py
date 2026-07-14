import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.observability_events import (
    include_frontend_message,
    include_response_body,
    log_event,
    summarize_created_invoice,
    summarize_invoice_draft,
    summarize_response,
)
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
from app.services.document_template_fields import invoice_fields_to_show
from app.services.invoice_draft_validator import (
    find_missing_invoice_fields,
    invoice_draft_to_create,
)
from app.services.invoice_service import InvoiceNumberConflictError, create_invoice
from app.services.llm_client import LlmServiceError

logger = logging.getLogger(__name__)

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
    log_event(
        "invoice.extract.llm.started",
        **include_frontend_message(message),
    )
    try:
        draft = await ai_invoice_extractor.extract(message)
    except LlmServiceError as error:
        response_body = {
            "status": "llm_unavailable",
            "message": LLM_UNAVAILABLE_MESSAGE,
        }
        log_event(
            "invoice.extract.llm_unavailable",
            level=logging.WARNING,
            error_type=type(error).__name__,
            error=str(error),
            **include_frontend_message(message),
            **include_response_body(response_body),
        )
        return JSONResponse(status_code=503, content=response_body)
    except AiInvoiceParseError as error:
        response_body = {
            "status": "ai_parse_error",
            "message": AI_PARSE_ERROR_MESSAGE,
        }
        log_event(
            "invoice.extract.parse_error",
            level=logging.WARNING,
            error_type=type(error).__name__,
            error=str(error),
            **include_frontend_message(message),
            **include_response_body(response_body),
        )
        return JSONResponse(status_code=422, content=response_body)

    log_event(
        "invoice.extract.llm.completed",
        **include_frontend_message(message),
        draft=summarize_invoice_draft(draft),
    )
    return draft


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
    log_event(
        "invoice.extract.received",
        **include_frontend_message(payload.message),
    )
    draft = await _extract_draft_or_error(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    missing_fields = find_missing_invoice_fields(draft)
    fields_to_show = invoice_fields_to_show(missing_fields)
    response = AiInvoiceExtractResponse(
        status="missing_fields" if missing_fields else "ready",
        draft=draft,
        missing_fields=missing_fields,
        fields_to_show=fields_to_show,
    )
    log_event(
        "invoice.extract.response.sent",
        status=response.status,
        missing_fields=missing_fields,
        fields_to_show=[field.model_dump() for field in fields_to_show],
        draft=summarize_invoice_draft(draft),
        **include_response_body(summarize_response(response)),
    )
    return response


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
    log_event(
        "invoice.generate.received",
        **include_frontend_message(payload.message),
    )
    draft = await _extract_draft_or_error(payload.message)
    if isinstance(draft, JSONResponse):
        return draft

    missing_fields = find_missing_invoice_fields(draft)
    if missing_fields:
        fields_to_show = invoice_fields_to_show(missing_fields)
        response = InvoiceDraftMissingResponse(
            status="missing_fields",
            missing_fields=missing_fields,
            fields_to_show=fields_to_show,
        )
        log_event(
            "invoice.generate.response.sent",
            level=logging.WARNING,
            status=response.status,
            missing_fields=missing_fields,
            fields_to_show=[field.model_dump() for field in fields_to_show],
            draft=summarize_invoice_draft(draft),
            **include_response_body(summarize_response(response)),
        )
        return response

    invoice = invoice_draft_to_create(draft)
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        log_event(
            "invoice.generate.conflict",
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
        "invoice.generate.response.sent",
        status=response.status,
        created_invoice=summarize_created_invoice(created_invoice),
        **include_response_body(summarize_response(response)),
    )
    return response
