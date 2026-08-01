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
                "issue_date": date(20>xë»h‘éì¶»§q«^t€€€€€€€€€Ý¥Ñ Í•±˜¹…ÍÍ•ÉÑI…¥Í•Ì¡!QQAá•ÁÑ¥½¸¤…Ì½¹Ñ•áÐè4(€€€€€€€€€€€€€€€É•…Ñ•}¥¹Ù½¥•}•¹‘Á½¥¹Ð¡½µÁ±•Ñ•}¥¹Ù½¥” ¤¤4(4(€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡½¹Ñ•áÐ¹•á•ÁÑ¥½¸¹ÍÑ…ÑÕÍ}½‘”°€ÐÀä¤4(€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡½¹Ñ•áÐ¹•á•ÁÑ¥½¸¹‘•Ñ…¥°°€‰%¹Ù½¥”¹Õµ‰•È•á¥ÍÑÌˆ¤4(4(€€€‘•˜Ñ•ÍÑ}±¥ÍÑ}…¹‘}É•Í•Ñ}ÕÍ•}‘½Õµ•¹Ñ}‰½Õ¹‘…Éä¡Í•±˜¤€´ø9½¹”è4(€€€€€€€±¥ÍÑ•€ô%¹Ù½¥•1¥ÍÑ%Ñ•´ 4(€€€€€€€€€€€¥ôÜ°4(€€€€€€€€€€€¥¹Ù½¥•}¹Õµ‰•Èô‰%9X´ÀÀÄˆ°4(€€€€€€€€€€€¥ÍÍÕ•}‘…Ñ”õ‘…Ñ” ÈÀÈØ°€Ø°€ÄÔ¤°4(€€€€€€€€€€€‘Õ•}‘…Ñ”õ9½¹”°4(€€€€€€€€€€€ÕÉÉ•¹äô‰UMˆ°4(€€€€€€€€€€€‰ÕÍ¥¹•ÍÍ}¹…µ”ô‰M…É¥ÌMÑÕ‘¥¼ˆ°4(€€€€€€€€€€€±¥•¹Ñ}¹…µ”ô‰±•àˆ°4(€€€€€€€€€€€Ñ½Ñ…°õ•¥µ…° ˆÌÀÀ¸ÀÀˆ¤°4(€€€€€€€€€€€Á‘™}ÕÉ°ôˆ½•¹•É…Ñ•½¥¹Ù½¥•Ì½™¥±”¹Á‘˜ˆ°4(€€€€€€€€€€€É•…Ñ•‘}…Ðõ‘…Ñ•Ñ¥µ” ÈÀÈØ°€Ü°€ÌÄ°€ÄÈ°€À°€À¤°4(€€€€€€€€¤4(€€€€€€€É•Í•Ð€ôì‰‘•±•Ñ•‘}¥¹Ù½¥•Ìˆè€Ä°€‰‘•±•Ñ•‘}¥Ñ•µÌˆè€Åô4(4(€€€€€€€Ý¥Ñ Á…Ñ  ‰‘½Õµ•¹Ñ}Í•ÉÙ¥”¹…Á¤¹É½ÕÑ•Ì¹¥¹Ù½¥•Ì¹‘½Õµ•¹Ñ}…ÁÁ±¥…Ñ¥½¸ˆ¤…Ì…ÁÁ±¥…Ñ¥½¸è4(€€€€€€€€€€€…ÁÁ±¥…Ñ¥½¸¹±¥ÍÑ}‘½Õµ•¹ÑÌ¹É•ÑÕÉ¹}Ù…±Õ”€ôm±¥ÍÑ•‘t4(€€€€€€€€€€€…ÁÁ±¥…Ñ¥½¸¹É•Í•Ñ}‘½Õµ•¹ÑÌ¹É•ÑÕÉ¹}Ù…±Õ”€ôÉ•Í•Ð4(4(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡±¥ÍÑ}¥¹Ù½¥•Í}•¹‘Á½¥¹Ð ¤°m±¥ÍÑ•‘t¤4(€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•Í•Ñ}¥¹Ù½¥•Í}•¹‘Á½¥¹Ð ¤°É•Í•Ð¤4(4(€€€‘•˜Ñ•ÍÑ}‘½Ý¹±½…‘}ÕÍ•Í}‰½Õ¹‘…Éå}‘•ÍÉ¥ÁÑ½È¡Í•±˜¤€´ø9½¹”è4(€€€€€€€‘•ÍÉ¥ÁÑ½È€ô½Ý¹±½…‘•ÍÉ¥ÁÑ½È 4(€€€€€€€€€€€‘½Õµ•¹Ñ}¥ôÜ°4(€€€€€€€€€€€™¥±•¹…µ”ô‰¥¹Ù½¥”¹Á‘˜ˆ°4(€€€€€€€€€€€µ•‘¥…}ÑåÁ”ô‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ°4(€€€€€€€€€€€Á…Ñ õA…Ñ ¡}}™¥±•}|¤°4(€€€€€€€€¤4(4(€€€€€€€Ý¥Ñ Á…Ñ  ‰‘½Õµ•¹Ñ}Í•ÉÙ¥”¹…Á¤¹É½ÕÑ•Ì¹¥¹Ù½¥•Ì¹‘½Õµ•¹Ñ}…ÁÁ±¥…Ñ¥½¸ˆ¤…Ì…ÁÁ±¥…Ñ¥½¸è4(€€€€€€€€€€€…ÁÁ±¥…Ñ¥½¸¹•Ñ}‘½Ý¹±½…‘}‘•ÍÉ¥ÁÑ½È¹É•ÑÕÉ¹}Ù…±Õ”€ô‘•ÍÉ¥ÁÑ½È4(€€€€€€€€€€€É•ÍÁ½¹Í”€ô‘½Ý¹±½…‘}¥¹Ù½¥•}•¹‘Á½¥¹Ð Ü¤4(4(€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%Í%¹ÍÑ…¹”¡É•ÍÁ½¹Í”°¥±•I•ÍÁ½¹Í”¤4(€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•ÍÁ½¹Í”¹Á…Ñ °‘•ÍÉ¥ÁÑ½È¹Á…Ñ ¤4(€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡É•ÍÁ½¹Í”¹µ•‘¥…}ÑåÁ”°€‰…ÁÁ±¥…Ñ¥½¸½Á‘˜ˆ¤4(€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ ‰¥¹Ù½¥”¹Á‘˜ˆ°É•ÍÁ½¹Í”¹¡•…‘•ÉÍl‰½¹Ñ•¹Ðµ‘¥ÍÁ½Í¥Ñ¥½¸‰t¤4(4(€€€‘•˜Ñ•ÍÑ}‘½Ý¹±½…‘}ÁÉ•Í•ÉÙ•Í}µ¥ÍÍ¥¹}É•½É‘}…¹‘}™¥±•}‘•Ñ…¥±Ì¡Í•±˜¤€´ø9½¹”è4(€€€€€€€Ý¥Ñ Á…Ñ  ‰‘½Õµ•¹Ñ}Í•ÉÙ¥”¹…Á¤¹É½ÕÑ•Ì¹¥¹Ù½¥•Ì¹‘½Õµ•¹Ñ}…ÁÁ±¥…Ñ¥½¸ˆ¤…Ì…ÁÁ±¥…Ñ¥½¸è4(€€€€€€€€€€€™½È‘•Ñ…¥°¥¸€ ‰%¹Ù½¥”¹½Ð™½Õ¹ˆ°€‰•¹•É…Ñ•A™¥±”¹½Ð™½Õ¹ˆ¤è4(€€€€€€€€€€€€€€€…ÁÁ±¥…Ñ¥½¸¹•Ñ}‘½Ý¹±½…‘}‘•ÍÉ¥ÁÑ½È¹Í¥‘•}•™™•Ð€ô€ 4(€€€€€€€€€€€€€€€€€€€½Õµ•¹Ñ9½Ñ½Õ¹‘ÉÉ½È¡‘•Ñ…¥°¤4(€€€€€€€€€€€€€€€€¤4(€€€€€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡‘•Ñ…¥°õ‘•Ñ…¥°¤è4(€€€€€€€€€€€€€€€€€€€Ý¥Ñ Í•±˜¹…ÍÍ•ÉÑI…¥Í•Ì¡!QQAá•ÁÑ¥½¸¤…Ì½¹Ñ•áÐè4(€€€€€€€€€€€€€€€€€€€€€€€‘½Ý¹±½…‘}¥¹Ù½¥•}•¹‘Á½¥¹Ð Ü¤4(€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡½¹Ñ•áÐ¹•á•ÁÑ¥½¸¹ÍÑ…ÑÕÍ}½‘”°€ÐÀÐ¤4(€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡½¹Ñ•áÐ¹•á•ÁÑ¥½¸¹‘•Ñ…¥°°‘•Ñ…¥°¤4(4(4)¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè4(€€€Õ¹¥ÑÑ•ÍÐ¹µ…¥¸ ¤4