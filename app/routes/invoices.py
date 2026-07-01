import json

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

    prompt = (
        "Convert the user's invoice item text into JSON invoice items. "
        "Understand natural wording such as 'x2', '2 times', 'count: 2', "
        "'qty 2', and similar quantity phrases. "
        "Use quantity as the count and unit_price as the price per one unit. "
        "Keep descriptions concise and do not include the price in the description.\n"
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is temporarily unavailable. Please try again later.",
        ) from error
    except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not understand invoice items. Please rewrite the items with descriptions, quantities, and prices.",
        ) from error

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
    draft = await _normalize_raw_invoice_items(payload.draft)
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


@router.post("", response_model=InvoiceCreateResponse, status_code=status.HTTP_201_CREATED)
def create_invoice_endpoint(invoice: InvoiceCreate) -> dict:
    try:
        return create_invoice(invoice)
    except InvoiceNumberConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("", response_model=list[InvoiceListItem])
def list_invoices_endpoint() -> list[dict]:
    return list_invoices()


@router.delete("")
def reset_invoices_endpoint() -> dict:
    return reset_invoice_store()


@router.get("/{invoice_id}/download", response_class=FileResponse)
def download_invoice_endpoint(invoice_id: int) -> FileResponse:
    pdf_path = get_invoice_pdf_path(invoice_id)
    if pdf_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if not pdf_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generated PDF file not found",
        )

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
    )
