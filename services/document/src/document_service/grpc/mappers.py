from document_service.schemas.results import DocumentCreated, DraftAnalysis
from document_service.schemas import InvoiceDraft, InvoiceListItem
from backend_contracts.documents.v1 import document_service_pb2 as pb2


def _text(value) -> str:
    return "" if value is None else str(value)


def _optional(value: str) -> str | None:
    return value or None


def draft_from_proto(draft: pb2.DocumentDraft) -> InvoiceDraft:
    return InvoiceDraft.model_validate(
        {
            "document_type": draft.document_type or "invoice",
            "invoice_number": _optional(draft.invoice_number),
            "issue_date": _optional(draft.issue_date),
            "due_date": _optional(draft.due_date),
            "currency": _optional(draft.currency),
            "template_language": _optional(draft.template_language),
            "business": {
                "name": _optional(draft.business.name),
                "email": _optional(draft.business.email),
                "address": _optional(draft.business.address),
            },
            "client": {
                "name": _optional(draft.client.name),
                "email": _optional(draft.client.email),
                "address": _optional(draft.client.address),
            },
            "items": [
                {
                    "description": _optional(item.description),
                    "quantity": _optional(item.quantity_decimal),
                    "unit_price": _optional(item.unit_price.decimal_value),
                }
                for item in draft.items
            ],
            "raw_items": _optional(draft.raw_items),
            "notes": _optional(draft.notes),
            "payment_terms": _optional(draft.payment_terms),
        }
    )


def draft_to_proto(draft: InvoiceDraft) -> pb2.DocumentDraft:
    return pb2.DocumentDraft(
        document_type=draft.document_type,
        invoice_number=_text(draft.invoice_number),
        issue_date=_text(draft.issue_date),
        due_date=_text(draft.due_date),
        currency=_text(draft.currency),
        template_language=_text(draft.template_language),
        business=pb2.Party(
            name=_text(draft.business.name),
            email=_text(draft.business.email),
            address=_text(draft.business.address),
        ),
        client=pb2.Party(
            name=_text(draft.client.name),
            email=_text(draft.client.email),
            address=_text(draft.client.address),
        ),
        items=[
            pb2.DraftItem(
                description=_text(item.description),
                quantity_decimal=_text(item.quantity),
                unit_price=pb2.Money(
                    decimal_value=_text(item.unit_price),
                    currency=_text(draft.currency),
                ),
            )
            for item in draft.items
        ],
        raw_items=_text(draft.raw_items),
        notes=_text(draft.notes),
        payment_terms=_text(draft.payment_terms),
    )


def analysis_to_proto(analysis: DraftAnalysis) -> pb2.DraftAnalysis:
    return pb2.DraftAnalysis(
        status=analysis.status,
        draft=draft_to_proto(analysis.draft),
        missing_field_keys=analysis.missing_fields,
        fields_to_show=[
            pb2.DraftField(
                key=field.key,
                label=field.label,
                field_type=field.type,
                required=field.required,
                placeholder=_text(field.placeholder),
                options=field.options or [],
            )
            for field in analysis.fields_to_show
        ],
    )


def summary_to_proto(document: InvoiceListItem) -> pb2.DocumentSummary:
    return pb2.DocumentSummary(
        document_id=document.id,
        document_type="invoice",
        invoice_number=document.invoice_number,
        issue_date=document.issue_date.isoformat(),
        due_date=document.due_date.isoformat() if document.due_date else "",
        currency=document.currency,
        business_name=document.business_name,
        client_name=document.client_name,
        total=pb2.Money(
            decimal_value=str(document.total),
            currency=document.currency,
        ),
        pdf_url=document.pdf_url,
        created_at=document.created_at.isoformat(),
    )


def created_to_proto(document: DocumentCreated) -> pb2.DocumentCreated:
    return pb2.DocumentCreated(
        document_id=document.document_id,
        document_type=document.document_type,
        invoice_number=document.invoice_number,
        subtotal=pb2.Money(
            decimal_value=str(document.subtotal),
            currency=document.currency,
        ),
        total=pb2.Money(
            decimal_value=str(document.total),
            currency=document.currency,
        ),
        pdf_url=document.pdf_url,
        download_url=document.download_url,
    )
