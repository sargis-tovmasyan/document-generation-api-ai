from __future__ import annotations

from app.schemas import DynamicFormField


INVOICE_TEMPLATE_ID = "standard_invoice"
INVOICE_DOCUMENT_TYPE = "invoice"

_INVOICE_FIELD_DEFINITIONS: dict[str, DynamicFormField] = {
    "invoice_number": DynamicFormField(
        key="invoice_number",
        label="Invoice number",
        type="text",
        required=True,
        placeholder="INV-001",
    ),
    "issue_date": DynamicFormField(
        key="issue_date",
        label="Issue date",
        type="date",
        required=True,
    ),
    "currency": DynamicFormField(
        key="currency",
        label="Currency",
        type="select",
        required=True,
        options=["USD", "EUR", "AMD", "RUB", "GBP"],
        placeholder="USD",
    ),
    "business.name": DynamicFormField(
        key="business.name",
        label="Business name",
        type="text",
        required=True,
        placeholder="Your company name",
    ),
    "client.name": DynamicFormField(
        key="client.name",
        label="Client name",
        type="text",
        required=True,
        placeholder="Client or company name",
    ),
    "items": DynamicFormField(
        key="items",
        label="Invoice items",
        type="line_items",
        required=True,
        placeholder="Website design x1 - 300",
    ),
}


def invoice_fields_to_show(missing_fields: list[str]) -> list[DynamicFormField]:
    """Return frontend-renderable field metadata for missing invoice fields.

    The backend still validates the draft against the invoice template. This
    function only converts missing template field paths into UI metadata so the
    frontend can render forms from the template instead of hardcoding invoice
    fields.
    """

    normalized_missing = set(missing_fields)
    if any(field == "items" or field.startswith("items[") for field in normalized_missing):
        normalized_missing.add("items")
        normalized_missing = {field for field in normalized_missing if not field.startswith("items[")}

    return [
        field_definition
        for field_key, field_definition in _INVOICE_FIELD_DEFINITIONS.items()
        if field_key in normalized_missing
    ]
