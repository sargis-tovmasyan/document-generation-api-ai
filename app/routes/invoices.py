import json
import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.observability_events import (
    include_response_body,
    log_event,
    summarize_created_invoice,
    summarize_invoice_draft,
    summarize_response,
)
from app.schemas import (
    InvoiceCreate,
    InvoiceCreateResponse,
    InvoiceDraftCompleteRequest,
    InvoiceDraftCreatedResponse,
    InvoiceDraft,
    InvoiceDraftItem,
    InvoiceDraftMissingResponse,
    InvoiceListItem,
)
from app.services.invoice_draft_validator import (
    find_missing_invoice_fields,
    invoice_draft_to_create,
)
from app.services.invoice_service import (
    InvoiceNumberConflictError,
    create_invoice,
    get_invoice_pdf_path,
    list_invoices,
    reset_invoice_store,
)
from app.services.llm_client import LlmServiceError, llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

ITEM_NORMALIZATION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                },
                "required": ["description", "quantity", "unit_price"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


async def _normalize_raw_invoice_items(draft: InvoiceDraft) -> InvoiceDraft:
    if not draft.raw_items:
        return draft

    log_event(
        "invoice.items.normalize.started",
        raw_items_length=len(draft.raw_items),
        draft=summarize_invoice_draft(draft),
    )
    prompt = (
        "Convert the user's invoice item text into JSON invoice items. "
        "Understand natural wording such as 'x2', '2 times', 'count: 2', "
        "'qty 2', and similar quantity phrases. "
        "Use quantity as the count. Use the amount after '-' or ':' as unit_price "
        "unless the user explicitly says it is the total price. "
        "Keep descriptions concise and do not include quantity or price in the description. "
        "Example: 'Sharuma x1 - 1200, Qyabab x2 - 1100' becomes "
        '{"items":[{"description":"Sharuma","quantity":1,"unit_price":1200},'
        '{"description":"Qyabab","quantity":2,"unit_price":1100}]}. '
        "Example: 'service, count: 2, price 500' becomes "
        '{"items":[{"description":"service","quantity":2,"unit_price":500}]}. '
        "\n"
        f"Item text: {draft.raw_items}\n"
        "JSON:"
    )
    try:
        content = await llm_client.complete_prompt(
            prompt,
            json_schema=ITEM_NORMALIZATION_SCHEMA,
            max_tokens=512,
        )
        raw = json.loads(content)
        items = [InvoiceDraftItem.model_validate(item) for item in raw["items"]]
    except LlmServiceError as error:
        log_event(
            "invoice.items.normalize.llm_unavailable",
            level=logging.WARNING,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is temporarily unavailable. Please try again later.",
        ) from error
    except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        log_event(
            "invoice.items.normalize.parse_error",
            level=logging.WARNING,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not understand invoice items. Please rewrite the items with descriptions, quantities, and prices.",
        ) from error

    data = draft.model_dump()
    data["items"] = [item.model_dump() for item in items]
    data["raw_items"] = None
    normalized_draft = InvoiceDraft.model_validate(data)
    log_event(
        "invoice.items.normalize.completed",
        item_count=len(items),
        draft=summarize_invoice_draft(normalized_draft),
    )
    return normalized_draft


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
    draft = await _normalize_raw_invoice_items(payload.draft)
    missing_fields = find_missing_invoice_fields(draft)
    log_event(
        "invoice.draft.validation.completed",
        missing_fields=missing_fields,
        draft=summarize_invoice_draft(draft),
    )
    if missing_fields:
        response = InvoiceDraftMissingResponse(
            status="missing_fields",
            missing_fields=missing_fields,
        )
        log_event(
            "invoice.draft.complete.response.sent",
            level=logging.WARNING,
            status=response.status,
            missing_fields=missing_fields,
            **include_response_body(summarize_response(response)),
        )
        return response

    invoice = invoice_draft_to_create(draft)
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        log_event(
            "invoice.draft.complete.conflict",
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
        "invoice.draft.complete.response.sent",
        status=response.status,
        created_invoice=summarize_created_invoice(created_invoice),
        **include_response_body(summarize_response(response)),
    )
    return response


@router.post("", response_model=InvoiceCreateResponse, status_code=status.HTTP_201_CREATED)
def create_invoice_endpoint(invoice: InvoiceCreate) -> dict:
    log_event(
        "invoice.create.received",
        invoice_number=invoice.invoice_number,
        draft=summarize_invoice_draft(invoice),
    )
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        log_event(
            "invoice.create.conflict",
            level=logging.WARNING,
            invoice_number=invoice.invoice_number,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    log_event(
        "invoice.create.response.sent",
        created_invoice=summarize_created_invoice(created_invoice),
        **include_response_body(created_invoice),
    )
    return created_invoice


@router.get("", response_model=list[InvoiceListItem])
def list_invoices_endpoint() -> list[dict]:
    invoices = list_invoices()
    log_event(
        "invoice.list.response.sent",
        invoice_count=len(invoices),
        **include_response_body({"invoice_count": len(invoices), "invoices": invoices}),
    )
    return invoices


@router.delete("")
def reset_invoices_endpoint() -> dict:
    result = reset_invoice_store()
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
    pdf_path = get_invoice_pdf_path(invoice_id)
    if pdf_path is None:
        log_event(
            "invoice.download.not_found",
            level=logging.WARNING,
            invoice_id=invoice_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if not pdf_path.is_file():
        log_event(
            "invoice.download.pdf_missing",
            level=logging.WARNING,
            invoice_id=invoice_id,
            pdf_path=str(pdf_path),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generated PDF file not found",
        )

    log_event(
        "invoice.download.response.sent",
        invoice_id=invoice_id,
        filename=pdf_path.name,
        pdf_path=str(pdf_path),
    )
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
    )
