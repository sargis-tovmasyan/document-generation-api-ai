import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from document_service.services.application import DocumentApplicationService
from document_service.services.errors import (
    DocumentConflictError,
    DocumentExtractionInvalidError,
    DocumentExtractionUnavailableError,
    DocumentItemsInvalidError,
    DocumentItemsUnavailableError,
    DocumentNotFoundError,
)
from document_service.schemas import InvoiceCreate, InvoiceDraft
from document_service.services.ai_invoice_extractor import AiInvoiceParseError
from document_service.db.repositories.invoices import InvoiceNumberConflictError
from document_service.clients.llm import LlmServiceError


COMPLETE_DRAFT = InvoiceDraft.model_validate(
    {
        "invoice_number": "INV-001",
        "issue_date": "2026-07-31",
        "currency": "USD",
        "business": {"name": "Sargis Studio"},
        "client": {"name": "Alex"},
        "items": [
            {"description": "Website design", "quantity": 1, "unit_price": 300}
        ],
    }
)
COMPLETE_INVOICE = InvoiceCreate.model_validate(
    COMPLETE_DRAFT.model_dump(
        exclude={"document_type", "raw_items"},
        exclude_none=True,
    )
)

CREATED_INVOICE = {
    "id": 7,
    "invoice_number": "INV-001",
    "subtotal": Decimal("300.00"),
    "total": Decimal("300.00"),
    "currency": "USD",
    "pdf_url": "/generated/invoices/inv-001.pdf",
}


class StubExtractor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    async def extract(self, _message: str):
        if self.error:
            raise self.error
        return self.result


class StubLlmClient:
    def __init__(self, result: str = "", error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    async def complete_prompt(self, *_args, **_kwargs) -> str:
        if self.error:
            raise self.error
        return self.result


class DocumentApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_and_analyze_returns_missing_field_metadata(self) -> None:
        draft = InvoiceDraft.model_validate({"client": {"name": "Alex"}})
        service = DocumentApplicationService(extractor=StubExtractor(draft))

        result = await service.extract_and_analyze("Create an invoice")

        self.assertEqual(result.status, "missing_fields")
        self.assertIn("invoice_number", result.missing_fields)
        self.assertIn("invoice_number", [field.key for field in result.fields_to_show])

    async def test_extract_and_analyze_returns_ready_for_complete_draft(self) -> None:
        service = DocumentApplicationService(extractor=StubExtractor(COMPLETE_DRAFT))

        result = await service.extract_and_analyze("Create an invoice")

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.missing_fields, [])

    async def test_extract_draft_maps_unavailable_extractor_error(self) -> None:
        service = DocumentApplicationService(
            extractor=StubExtractor(error=LlmServiceError("offline"))
        )

        with self.assertRaises(DocumentExtractionUnavailableError):
            await service.extract_draft("Create an invoice")

    async def test_extract_draft_maps_invalid_extractor_error(self) -> None:
        service = DocumentApplicationService(
            extractor=StubExtractor(error=AiInvoiceParseError("bad JSON"))
        )

        with self.assertRaises(DocumentExtractionInvalidError):
            await service.extract_draft("Create an invoice")

    async def test_generate_from_message_creates_complete_draft(self) -> None:
        service = DocumentApplicationService(
            extractor=StubExtractor(COMPLETE_DRAFT),
            create_document=lambda _invoice: CREATED_INVOICE,
        )

        completion = await service.generate_from_message("Create an invoice")

        self.assertIsNone(completion.analysis)
        self.assertEqual(completion.created.document_id, 7)
        self.assertEqual(completion.created.pdf_url, CREATED_INVOICE["pdf_url"])
        self.assertEqual(completion.created.download_url, "/invoices/7/download")

    async def test_complete_draft_normalizes_raw_items_before_creation(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
                **COMPLETE_DRAFT.model_dump(exclude={"items"}),
                "items": [],
                "raw_items": "Website design x2 - 300",
            }
        )
        captured = {}

        def create_document(invoice):
            captured["invoice"] = invoice
            return {**CREATED_INVOICE, "total": Decimal("600.00")}

        service = DocumentApplicationService(
            llm=StubLlmClient(
                '{"items":[{"description":"Website design",'
                '"quantity":2,"unit_price":300}]}'
            ),
            create_document=create_document,
        )

        completion = await service.complete_draft(draft)

        self.assertEqual(captured["invoice"].items[0].quantity, Decimal("2"))
        self.assertEqual(completion.created.total, Decimal("600.00"))

    async def test_complete_draft_maps_unavailable_item_normalization(self) -> None:
        draft = InvoiceDraft.model_validate(
            {**COMPLETE_DRAFT.model_dump(exclude={"items"}), "raw_items": "service"}
        )
        service = DocumentApplicationService(
            llm=StubLlmClient(error=LlmServiceError("offline"))
        )

        with self.assertRaises(DocumentItemsUnavailableError):
            await service.complete_draft(draft)

    async def test_complete_draft_maps_invalid_item_normalization(self) -> None:
        draft = InvoiceDraft.model_validate(
            {**COMPLETE_DRAFT.model_dump(exclude={"items"}), "raw_items": "service"}
        )
        service = DocumentApplicationService(llm=StubLlmClient("not JSON"))

        with self.assertRaises(DocumentItemsInvalidError):
            await service.complete_draft(draft)

    def test_create_document_maps_duplicate_invoice_number(self) -> None:
        def duplicate(_invoice):
            raise InvoiceNumberConflictError("Invoice number exists")

        service = DocumentApplicationService(create_document=duplicate)

        with self.assertRaisesRegex(DocumentConflictError, "Invoice number exists"):
            service.create_document(COMPLETE_INVOICE)

    def test_list_and_reset_documents_preserve_storage_results(self) -> None:
        listed = [
            {
                "id": 7,
                "invoice_number": "INV-001",
                "issue_date": date(2026, 7, 31),
                "due_date": None,
                "currency": "USD",
                "business_name": "Sargis Studio",
                "client_name": "Alex",
                "total": Decimal("300.00"),
                "pdf_url": "/generated/invoices/inv-001.pdf",
                "created_at": datetime(2026, 7, 31, 12, 0, 0),
            }
        ]
        reset = {"deleted_invoices": 1, "deleted_items": 1}
        service = DocumentApplicationService(
            list_documents=lambda: listed,
            reset_documents=lambda: reset,
        )

        self.assertEqual(service.list_documents()[0].invoice_number, "INV-001")
        self.assertEqual(service.reset_documents(), reset)

    def test_get_document_returns_match_and_rejects_unknown_id(self) -> None:
        listed = [
            {
                "id": 7,
                "invoice_number": "INV-001",
                "issue_date": date(2026, 7, 31),
                "due_date": None,
                "currency": "USD",
                "business_name": "Sargis Studio",
                "client_name": "Alex",
                "total": Decimal("300.00"),
                "pdf_url": "/generated/invoices/inv-001.pdf",
                "created_at": datetime(2026, 7, 31, 12, 0, 0),
            }
        ]
        service = DocumentApplicationService(list_documents=lambda: listed)

        self.assertEqual(service.get_document(7).invoice_number, "INV-001")
        with self.assertRaisesRegex(DocumentNotFoundError, "Invoice not found"):
            service.get_document(8)

    def test_download_descriptor_distinguishes_record_and_file_missing(
        self,
    ) -> None:
        missing_record = DocumentApplicationService(get_pdf_path=lambda _id: None)
        missing_file = DocumentApplicationService(
            get_pdf_path=lambda _id: Path("missing-invoice.pdf")
        )

        with self.assertRaisesRegex(DocumentNotFoundError, "Invoice not found"):
            missing_record.get_download_descriptor(7)
        with self.assertRaisesRegex(
            DocumentNotFoundError, "Generated PDF file not found"
        ):
            missing_file.get_download_descriptor(7)

    def test_download_descriptor_returns_existing_file(self) -> None:
        pdf_path = Path(__file__)
        service = DocumentApplicationService(get_pdf_path=lambda _id: pdf_path)

        descriptor = service.get_download_descriptor(7)

        self.assertEqual(descriptor.document_id, 7)
        self.assertEqual(descriptor.filename, pdf_path.name)
        self.assertEqual(descriptor.media_type, "application/pdf")
        self.assertEqual(descriptor.path, pdf_path)


if __name__ == "__main__":
    unittest.main()
