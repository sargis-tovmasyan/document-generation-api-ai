import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from document_service.services.application import document_application
from document_service.services.errors import (
    DocumentConflictError,
    DocumentExtractionInvalidError,
    DocumentExtractionUnavailableError,
)
from document_service.schemas.results import DocumentCreated
from document_service.core.events import (
    include_frontend_message,
    include_response_body,
    log_event,
    summarize_created_invoice,
    summarize_invoice_draft,
    summarize_response,
)
from document_service.schemas import (
    AiInvoiceErrorResponse,
    AiInvoiceExtractRequest,
    AiInvoiceExtractResponse,
    InvoiceDraft,
    InvoiceDraftCreatedResponse,
    InvoiceDraftMissingResponse,
)

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


def _extraction_error_response(
    error: DocumentExtractionUnavailableError | DocumentExtractionInvalidError,
    message: str,
) -> JSONResponse:
    if isinstance(error, DocumentExtractionUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        response_body = {
            "status": "llm_unavailable",
            "message": LLM_UNAVAILABLE_MESSAGE,
        }
        event_name = "invoice.extract.llm_unavailable"
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        response_body = {
            "status": "ai_parse_error",
            "message": AI_PARSE_ERROR_MESSAGE,
        }
        event_name = "invoice.extract.parse_error"

    log_event(
        event_name,
        level=logging.WARNING,
        error_type=type(error).__name__,
        error=str(error),
        **include_frontend_message(message),
        **include_response_body(response_body),
    )
    return JSONResponse(status_code=status_code, content=response_body)


async def _extract_draft_or_error(message: str) -> InvoiceDraft | JSONResponse:
    log_event(
        "invoice.extract.llm.started",
        **include_frontend_message(message),
    )
    try:
        draft = await document_application.extract_draft(message)
    except (DocumentExtractionUnavailableError, DocumentExtractionInvalidError) as error:
        return _extraction_error_response(error, message)

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
    try:
        analysis = await document_application.extract_and_analyze(payload.message)
    except (DocumentExtractionUnavailableError, DocumentExtractionInvalidError) as error:
        return _extraction_error_response(error, payload.message)

    response = AiInvoiceExtractResponse(
        status=analysis.status,
        draft=analysis.draft,
        missing_fields=analysis.missing_fields,
        fields_to_show=analysis.fields_to_show,
    )
    log_event(
        "invoice.extract.response.sent",
        status=response.status,
        missing_fields=response.missing_fields,
        fields_to_show=[field.model_dump() for field in response.fields_to_show],
        draft=summarize_invoice_draft(response.draft),
        **include_response_body(summarize_response(response)),
    )
    return response


def _created_invoice_summary(created: DocumentCreated) -> dict:
    return {
        "id": created.document_id,
        "invoice_number": created.invoice_number,
        "subtotal": created.subtotal,
        "total": created.total,
        "currency": created.currency,
        "pdf_url": created.pdf_url,
    }


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
    try:
        completion = await document_application.generate_from_message(payload.message)
    except (DocumentExtractionUnavailableError, DocumentExtractionInvalidError) as error:
        return _extraction_error_response(error, payload.message)
    except DocumentConflictError as error:
        log_event(
            "invoice.generate.conflict",
            level=logging.WARNING,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    if completion.analysis is not None:
        analysis = completion.analysis
        response = InvoiceDraftMissingResponse(
            status="missing_fields",
            missing_fields=analysis.missing_fields,
            fields_to_show=analysis.fields_to_show,
        )
        log_event(
            "invoice.generate.response.sent",
            level=logging.WARNING,
            status=response.status,
            missing_fields=analysis.missing_fields,
            fields_to_show=[
                field.model_dump() for field in analysis.fields_to_show
            ],
            draft=summarize_invoice_draft(analysis.draft),
            **include_response_body(summarize_response(response)),
        )
        return response

    created = completion.created
    response = InvoiceDraftCreatedResponse(
        status="created",
        invoice_id=created.document_id,
        invoice_number=created.invoice_number,
        subtotal=created.subtotal,
        total=created.total,
        currency=created.currency,
        pdf_url=created.download_url,
    )
    log_event(
        "invoice.generate.response.sent",
        status=response.status,
        created_invoice=summarize_created_invoice(
            _created_invoice_summary(created)
        ),
        **include_response_body(summarize_response(response)),
    )
    return response
