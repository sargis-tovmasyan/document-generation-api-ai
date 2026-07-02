import json
import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

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

    logger.info("invoice.items.normalize.started raw_items_length=%s", len(draft.raw_items))
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
        '{"items":[{"description":"service","quantity":2,"unit_price":500}]}.'
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
        logger.exception("invoice.items.normalize.llm_unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is temporarily unavailable. Please try again later.",
        ) from error
    except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        logger.exception("invoice.items.normalize.parse_error")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not understand invoice items. Please rewrite the items with descriptions, quantities, and prices.",
        ) from error

    logger.info("invoice.items.normalize.completed item_count=%s", len(items))
    data = draft.model_dump()
    data["items"] = [item.model_dump() for item in items]
    data["raw_items"] = None
    return InvoiceDraft.model_validate(data)


@router.post(
    "/draft/complete",
    response_model=InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse,
)
async def complete_invoice_draft(
    payload: InvoiceDraftCompleteRequest,
) -> InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse:
    logger.info("invoice.draft.complete.started")
    draft = await _normalize_raw_invoice_items(payload.draft)
    missing_fields = find_missing_invoice_fields(draft)
    if missing_fields:
        logger.info("invoice.draft.complete.missing_fields missing_fields=%s", missing_fields)
        return InvoiceDraftMissingResponse(
            status="missing_fields",
            missing_fields=missing_fields,
        )

    invoice = invoice_draft_to_create(draft)
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        logger.warning("invoice.draft.complete.conflict invoice_number=%s", invoice.invoice_number)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    logger.info(
        "invoice.draft.complete.created invoice_id=%s invoice_number=%s total=%s currency=%s",
        created_invoice["id"],
        created_invoice["invoice_number"],
        created_invoice["total"],
        created_invoice["currency"],
    )
    return InvoiceDraftCreatedResponse(
        status="created",
        invoice_id=created_invoice["id"],
        invoice_number=created_invoice["invoice_number"],
        subtotal=created_invoice["subtotal"],
        total=created_invoice["total"],
        currency=created_invoice["currency"],
        pdf_url=f"/invoices/{created_invoice['id']}/download",
    )


@router.post("", response_model=InvoiceCreateResponse, status_code=status.HTTP_201_CREATED)
def create_invoice_endpoint(invoice: InvoiceCreate) -> dict:
    logger.info("invoice.create.started invoice_number=%s", invoice.invoice_number)
    try:
        created_invoice = create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        logger.warning("invoice.create.conflict invoice_number=%s", invoice.invoice_number)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    logger.info(
        "invoice.create.completed invoice_id=%s invoice_number=%s total=%s currency=%s",
        created_invoice["id"],
        created_invoice["invoice_number"],
        created_invoice["total"],
        created_invoice["currency"],
    )
    return created_invoice


@router.get("", response_model=list[InvoiceListItem])
def list_invoices_endpoint() -> list[dict]:
    invoices = list_invoices()
    logger.info("invoice.list.completed count=%s", len(invoices))
    return invoices


@router.delete("")
def reset_invoices_endpoint() -> dict:
    result = reset_invoice_store()
    logger.warning("invoice.reset.completed result=%s", result)
    return result


@router.get("/{invoice_id}/download", response_class=FileResponse)
def download_invoice_endpoint(invoice_id: int) -> FileResponse:
    logger.info("invoice.download.started invoice_id=%s", invoice_id)
    pdf_path = get_invoice_pdf_path(invoice_id)
    if pdf_path is None:
        logger.warning("invoice.download.not_found invoice_id=%s", invoice_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if not pdf_path.is_file():
        logger.warning("invoice.download.pdf_missing invoice_id=%s path=%s", invoice_id, pdf_path)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generated PDF file not found",
        )

    logger.info("invoice.download.completed invoice_id=%s filename=%s", invoice_id, pdf_path.name)
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
    )
