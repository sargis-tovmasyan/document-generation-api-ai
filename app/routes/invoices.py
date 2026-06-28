from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.schemas import (
    InvoiceCreate,
    InvoiceCreateResponse,
    InvoiceDraftCompleteRequest,
    InvoiceDraftCreatedResponse,
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

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post(
    "/draft/complete",
    response_model=InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse,
)
def complete_invoice_draft(
    payload: InvoiceDraftCompleteRequest,
) -> InvoiceDraftCreatedResponse | InvoiceDraftMissingResponse:
    missing_fields = find_missing_invoice_fields(payload.draft)
    if missing_fields:
        return InvoiceDraftMissingResponse(
            status="missing_fields",
            missing_fields=missing_fields,
        )

    invoice = invoice_draft_to_create(payload.draft)
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
