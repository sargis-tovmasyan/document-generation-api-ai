from datetime import date, datetime
from decimal import Decimal

import grpc
from pydantic import BaseModel

from chat_service.core.config import DOCUMENT_GRPC_TARGET, DOCUMENT_GRPC_TIMEOUT_SECONDS
from chat_service.schemas import DynamicFormField, InvoiceDraft, InvoiceListItem
from backend_contracts.documents.v1 import document_service_pb2 as pb2
from backend_contracts.documents.v1 import document_service_pb2_grpc as pb2_grpc


class DocumentAnalysis(BaseModel):
    status: str
    draft: InvoiceDraft
    missing_fields: list[str]
    fields_to_show: list[DynamicFormField]


class DocumentCreated(BaseModel):
    document_id: int
    invoice_number: str
    subtotal: Decimal
    total: Decimal
    currency: str
    pdf_url: str
    download_url: str


class DocumentCompletion(BaseModel):
    analysis: DocumentAnalysis | None = None
    created: DocumentCreated | None = None


class DocumentClientError(Exception):
    pass


class DocumentUnavailableError(DocumentClientError):
    pass


class DocumentTimeoutError(DocumentUnavailableError):
    pass


class DocumentConflictError(DocumentClientError):
    pass


class DocumentInvalidError(DocumentClientError):
    pass


class DocumentNotFoundError(DocumentClientError):
    pass


def _optional(value: str) -> str | None:
    return value or None


def _draft_from_proto(draft: pb2.DocumentDraft) -> InvoiceDraft:
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


def _draft_to_proto(draft: InvoiceDraft) -> pb2.DocumentDraft:
    text = lambda value: "" if value is None else str(value)
    return pb2.DocumentDraft(
        document_type=draft.document_type,
        invoice_number=text(draft.invoice_number),
        issue_date=text(draft.issue_date),
        due_date=text(draft.due_date),
        currency=text(draft.currency),
        template_language=text(draft.template_language),
        business=pb2.Party(
            name=text(draft.business.name),
            email=text(draft.business.email),
            address=text(draft.business.address),
        ),
        client=pb2.Party(
            name=text(draft.client.name),
            email=text(draft.client.email),
            address=text(draft.client.address),
        ),
        items=[
            pb2.DraftItem(
                description=text(item.description),
                quantity_decimal=text(item.quantity),
                unit_price=pb2.Money(
                    decimal_value=text(item.unit_price),
                    currency=text(draft.currency),
                ),
            )
            for item in draft.items
        ],
        raw_items=text(draft.raw_items),
        notes=text(draft.notes),
        payment_terms=text(draft.payment_terms),
    )


def _analysis_from_proto(analysis: pb2.DraftAnalysis) -> DocumentAnalysis:
    return DocumentAnalysis(
        status=analysis.status,
        draft=_draft_from_proto(analysis.draft),
        missing_fields=list(analysis.missing_field_keys),
        fields_to_show=[
            DynamicFormField(
                key=field.key,
                label=field.label,
                type=field.field_type,
                required=field.required,
                placeholder=_optional(field.placeholder),
                options=list(field.options) or None,
            )
            for field in analysis.fields_to_show
        ],
    )


def _created_from_proto(created: pb2.DocumentCreated) -> DocumentCreated:
    return DocumentCreated(
        document_id=created.document_id,
        invoice_number=created.invoice_number,
        subtotal=Decimal(created.subtotal.decimal_value),
        total=Decimal(created.total.decimal_value),
        currency=created.total.currency or created.subtotal.currency,
        pdf_url=created.pdf_url,
        download_url=created.download_url,
    )


class DocumentClient:
    def __init__(self, target: str, *, timeout: float) -> None:
        self._target = target
        self._timeout = timeout
        self._channel = None
        self._stub = None

    async def __aenter__(self) -> "DocumentClient":
        self._ensure_stub()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    def _ensure_stub(self):
        if self._stub is None:
            self._channel = grpc.aio.insecure_channel(self._target)
            self._stub = pb2_grpc.DocumentServiceStub(self._channel)
        return self._stub

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None

    async def _call(self, operation):
        try:
            return await operation
        except grpc.aio.AioRpcError as error:
            message = error.details() or "Document service request failed"
            if error.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                raise DocumentTimeoutError(message) from error
            if error.code() == grpc.StatusCode.UNAVAILABLE:
                raise DocumentUnavailableError(message) from error
            if error.code() == grpc.StatusCode.ALREADY_EXISTS:
                raise DocumentConflictError(message) from error
            if error.code() == grpc.StatusCode.INVALID_ARGUMENT:
                raise DocumentInvalidError(message) from error
            if error.code() == grpc.StatusCode.NOT_FOUND:
                raise DocumentNotFoundError(message) from error
            raise DocumentClientError(message) from error

    async def list_documents(self, *, request_id: str) -> list[InvoiceListItem]:
        response = await self._call(
            self._ensure_stub().ListDocuments(
                pb2.ListDocumentsRequest(
                    request_id=request_id,
                    document_type="invoice",
                ),
                timeout=self._timeout,
                metadata=(("x-request-id", request_id),),
            )
        )
        return [
            InvoiceListItem(
                id=document.document_id,
                invoice_number=document.invoice_number,
                issue_date=date.fromisoformat(document.issue_date),
                due_date=(
                    date.fromisoformat(document.due_date)
                    if document.due_date
                    else None
                ),
                currency=document.currency,
                business_name=document.business_name,
                client_name=document.client_name,
                total=Decimal(document.total.decimal_value),
                pdf_url=document.pdf_url,
                created_at=datetime.fromisoformat(document.created_at),
            )
            for document in response.documents
        ]

    async def extract_draft(
        self, message: str, *, request_id: str
    ) -> DocumentAnalysis:
        response = await self._call(
            self._ensure_stub().ExtractDraft(
                pb2.ExtractDraftRequest(
                    request_id=request_id,
                    document_type="invoice",
                    message=message,
                ),
                timeout=self._timeout,
                metadata=(("x-request-id", request_id),),
            )
        )
        return _analysis_from_proto(response.analysis)

    async def complete_draft(
        self,
        draft: InvoiceDraft,
        *,
        request_id: str,
        idempotency_key: str,
    ) -> DocumentCompletion:
        response = await self._call(
            self._ensure_stub().CompleteDraft(
                pb2.CompleteDraftRequest(
                    request_id=request_id,
                    document_type="invoice",
                    idempotency_key=idempotency_key,
                    draft=_draft_to_proto(draft),
                ),
                timeout=self._timeout,
                metadata=(("x-request-id", request_id),),
            )
        )
        result = response.WhichOneof("result")
        if result == "analysis":
            return DocumentCompletion(
                analysis=_analysis_from_proto(response.analysis)
            )
        return DocumentCompletion(created=_created_from_proto(response.created))


document_client = DocumentClient(
    DOCUMENT_GRPC_TARGET,
    timeout=DOCUMENT_GRPC_TIMEOUT_SECONDS,
)
