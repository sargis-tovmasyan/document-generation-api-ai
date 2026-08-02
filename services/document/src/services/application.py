import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from services.errors import (
    DocumentConflictError,
    DocumentExtractionInvalidError,
    DocumentExtractionUnavailableError,
    DocumentItemsInvalidError,
    DocumentItemsUnavailableError,
    DocumentNotFoundError,
)
from schemas.results import (
    DocumentCreated,
    DownloadDescriptor,
    DraftAnalysis,
    DraftCompletion,
)
from core.events import log_event, summarize_invoice_draft
from schemas import InvoiceCreate, InvoiceDraft, InvoiceDraftItem, InvoiceListItem
from services.ai_invoice_extractor import (
    AiInvoiceParseError,
    ai_invoice_extractor,
)
from services.document_template_fields import invoice_fields_to_show
from services.invoice_draft_validator import (
    find_missing_invoice_fields,
    invoice_draft_to_create,
)
from db.repositories.invoices import (
    InvoiceNumberConflictError,
    create_invoice,
    get_invoice_pdf_path,
    list_invoices,
    reset_invoice_store,
)
from clients.llm import LlmServiceError, llm_client


logger = logging.getLogger(__name__)

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


class DocumentApplicationService:
    def __init__(
        self,
        *,
        extractor: Any = None,
        llm: Any = None,
        create_document: Callable[[InvoiceCreate], dict] | None = None,
        list_documents: Callable[[], list[dict]] | None = None,
        reset_documents: Callable[[], dict] | None = None,
        get_pdf_path: Callable[[int], Path | None] | None = None,
    ) -> None:
        self._extractor = extractor if extractor is not None else ai_invoice_extractor
        self._llm = llm if llm is not None else llm_client
        self._create_invoice = create_document or create_invoice
        self._list_invoices = list_documents or list_invoices
        self._reset_invoice_store = reset_documents or reset_invoice_store
        self._get_invoice_pdf_path = get_pdf_path or get_invoice_pdf_path

    async def extract_draft(self, message: str) -> InvoiceDraft:
        try:
            return await self._extractor.extract(message)
        except LlmServiceError as error:
            raise DocumentExtractionUnavailableError(str(error)) from error
        except AiInvoiceParseError as error:
            raise DocumentExtractionInvalidError(str(error)) from error

    def analyze_draft(self, draft: InvoiceDraft) -> DraftAnalysis:
        missing_fields = find_missing_invoice_fields(draft)
        return DraftAnalysis(
            status="missing_fields" if missing_fields else "ready",
            draft=draft,
            missing_fields=missing_fields,
            fields_to_show=invoice_fields_to_show(missing_fields),
        )

    async def extract_and_analyze(self, message: str) -> DraftAnalysis:
        return self.analyze_draft(await self.extract_draft(message))

    async def generate_from_message(self, message: str) -> DraftCompletion:
        return await self.complete_draft(await self.extract_draft(message))

    async def complete_draft(self, draft: InvoiceDraft) -> DraftCompletion:
        normalized_draft = await self._normalize_raw_invoice_items(draft)
        analysis = self.analyze_draft(normalized_draft)
        if analysis.missing_fields:
            return DraftCompletion(analysis=analysis)
        invoice = invoice_draft_to_create(normalized_draft)
        return DraftCompletion(created=self.create_document(invoice))

    def create_document(self, invoice: InvoiceCreate) -> DocumentCreated:
        try:
            created = self._create_invoice(invoice)
        except InvoiceNumberConflictError as error:
            raise DocumentConflictError(str(error)) from error
        document_id = created["id"]
        return DocumentCreated(
            document_id=document_id,
            invoice_number=created["invoice_number"],
            subtotal=created["subtotal"],
            total=created["total"],
            currency=created["currency"],
            pdf_url=created["pdf_url"],
            download_url=f"/invoices/{document_id}/download",
        )

    def list_documents(self) -> list[InvoiceListItem]:
        return [InvoiceListItem.model_validate(item) for item in self._list_invoices()]

    def get_document(self, document_id: int) -> InvoiceListItem:
        for document in self.list_documents():
            if document.id == document_id:
                return document
        raise DocumentNotFoundError("Invoice not found")

    def reset_documents(self) -> dict:
        return self._reset_invoice_store()

    def get_download_descriptor(self, document_id: int) -> DownloadDescriptor:
        pdf_path = self._get_invoice_pdf_path(document_id)
        if pdf_path is None:
            raise DocumentNotFoundError("Invoice not found")
        if not pdf_path.is_file():
            raise DocumentNotFoundError("Generated PDF file not found")
        return DownloadDescriptor(
            document_id=document_id,
            filename=pdf_path.name,
            media_type="application/pdf",
            path=pdf_path,
        )

    async def _normalize_raw_invoice_items(self, draft: InvoiceDraft) -> InvoiceDraft:
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
            content = await self._llm.complete_prompt(
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
            raise DocumentItemsUnavailableError(str(error)) from error
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
            log_event(
                "invoice.items.normalize.parse_error",
                level=logging.WARNING,
                error_type=type(error).__name__,
                error=str(error),
            )
            raise DocumentItemsInvalidError(str(error)) from error

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


document_application = DocumentApplicationService()
