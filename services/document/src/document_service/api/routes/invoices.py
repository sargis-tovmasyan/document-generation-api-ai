import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from document_service.services.application import document_application
from document_service.services.errors import (
    DocumentConflictError,
    DocumentItemsInvalidError,
    DocumentItemsUnavailableError,
    DocumentNotFoundError,
)
from document_service.schemas.results import DocumentCreated
from document_service.core.events import (
    include_response_body,
    log_event,
    summarize_created_invoice,
    summarize_invoice_draft,
    summarize_response,
)
from document_service.schemas import (
    InvoiceCreate,
    InvoiceCreateResponse,
    InvoiceDraftCompleteRequest,
    InvoiceDraftCreatedResponse,
    InvoiceDraftMissingResponse,
    InvoiceListItem,
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

ITEMS_UNAVAILABLE_DETAIL = (
    "AI assistant is temporarily unavailable. Please try again later."
)
ITEMS_INVALID_DETAIL = (
    "Could not understand invoice items. Please rewrite the items with "
    "descriptions, quantities, and prices."
)


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
    "/draft/complete",
    response_model=InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse,
)
async def complete_invoice_draft(
    payload: InvoiceDraftCompleteRequest,
) -> InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse:
    log_event(
        "invoice.draft.complete.received",
        draft=summarize_invoice_draft(payload.draft),
    )
    try:
        completion = await document_application.complete_draft(payload.draft)
    except DocumentItemsUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ITEMS_UNAVAILABLE_DETAIL,
        ) from error
    except DocumentItemsInvalidError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ITEMS_INVALID_DETAIL,
        ) from error
    except DocumentConflictError as error:
        log_event(
            "invoice.draft.complete.conflict",
            level=logging.WARNING,
            invoice_number=payload.draft.invoice_number,
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
            "invoice.draft.validation.completed",
            missing_fields=analysis.missing_fields,
            fields_to_show=[
                field.model_dump() for field in analysis.fields_to_show
            ],
            draft=summarize_invoice_draft(analysis.draft),
        )
        log_event(
            "invoice.draft.complete.response.sent",
            level=logging.WARNING,
            status=response.status,
            missing_fields=analysis.missing_fields,
            fields_to_show=[
                field.model_dump() for field in analysis.fields_to_show
            ],
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
        "invoice.draft.complete.response.sent",
        status=response.status,
        created_invoice=summarize_created_invoice(
            _created_invoice_summary(created)
        ),
        **include_response_body(summarize_response(response)),
    )
    return response


@router.post("", response_model=InvoiceCreateResponse, status_code=status.HTTP_201_CREATED)
def create_invoice_endpoint(invoice: InvoiceCreate) -> InvoiceCreateResponse:
    log_event(
        "invoice.create.received",
        invoice_number=invoice.invoice_number,
        draft=summarize_invoice_draft(invoice),
    )
    try:
        created = document_application.create_document(invoice)
    except DocumentConflictError as error:
        log_event(
            "invoice.create.conflict",
            level=logging.WARNING,
            invoice_number=invoice.invoice_number,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    response = InvoiceCreateResponse(
        id=created.document_id,
        invoice_number=created.invoice_number,
        total=created.total,
        pdf_url=created.pdf_url,
    )
    summary = _created_invoice_summary(created)
    log_event(
        "invoice.create.response.sent",
        created_invoice=summarize_created_invoice(summary),
        **include_response_body(response.model_dump(mode="json")),
    )
    return response


@router.get("", response_model=list[InvoiceListItem])
def list_invoices_endpoint() -> list[InvoiceListItem]:
    invoices = document_application.list_documents()
    log_event(
        "invoice.list.response.sent",
        invoice_count=len(invoices),
        **include_response_body(
            {
                "invoice_count": len(invoices),
                "invoices": [
                    invoice.model_dump(mode="json") for invoice in invoices
                ],
            }
        ),
    )
    return invoices


@router.delete("")
def reset_invoices_endpoint() -> dict:
    result = document_application.reset_documents()
    log_event(
        "invoice.reset.response.sent",
        level=logging.WARNING,
        **result,
        **include_response_body(result),
    )
    return result


@router.get("/{invoice_id}/download", response_class=FileResponse)
def download_invoice_endpoint(invoice_id: int) -> FileResponse:
    log_event("invoice.download.received", invoice_id=invoice_id)
    try:
        descriptor = document_application.get_download_descriptor(invoice_id)
    except DocumentNotFoundError as error:
        event_name = (
            "invoice.download.not_found"
            if str(error) == "Invoice not found"
            else "invoice.download.pdf_missing"
        )
        log_event(
            event_name,
            level=logging.WARNING,
            invoice_id=invoice_id,
            error=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    log_event(
        "invoice.download.response.sent",
        invoice_id=invoice_id,
        filename=descriptor.filename,
        pdf_path=str(descriptor.path),
    )
    return FileResponse(
        path=descriptor.path,
        media_type=descriptor.media_type,
        filename=descriptor.filename,
    )
