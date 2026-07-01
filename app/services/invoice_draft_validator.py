from app.schemas import InvoiceCreate, InvoiceDraft


def find_missing_invoice_fields(draft: InvoiceDraft) -> list[str]:
    missing_fields: list[str] = []

    if not draft.invoice_number:
        missing_fields.append("invoice_number")
    if draft.issue_date is None:
        missing_fields.append("issue_date")
    if not draft.currency:
        missing_fields.append("currency")
    if not draft.business.name:
        missing_fields.append("business.name")
    if not draft.client.name:
        missing_fields.append("client.name")
    if not draft.items:
        missing_fields.append("items")

    for index, item in enumerate(draft.items):
        if not item.description:
            missing_fields.append(f"items[{index}].description")
        if item.quantity is None:
            missing_fields.append(f"items[{index}].quantity")
        if item.unit_price is None:
            missing_fields.append(f"items[{index}].unit_price")

    return missing_fields


def invoice_draft_to_create(draft: InvoiceDraft) -> InvoiceCreate:
    return InvoiceCreate.model_validate(
        draft.model_dump(exclude={"document_type", "raw_items"}, exclude_none=True)
    )
