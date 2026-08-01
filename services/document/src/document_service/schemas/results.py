from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

from document_service.schemas import DynamicFormField, InvoiceDraft


class DraftAnalysis(BaseModel):
    status: Literal["missing_fields", "ready"]
    draft: InvoiceDraft
    missing_fields: list[str]
    fields_to_show: list[DynamicFormField]


class DocumentCreated(BaseModel):
    status: Literal["created"] = "created"
    document_id: int
    document_type: Literal["invoice"] = "invoice"
    invoice_number: str
    subtotal: Decimal
    total: Decimal
    currency: str
    pdf_url: str
    download_url: str


class DraftCompletion(BaseModel):
    analysis: DraftAnalysis | None = None
    created: DocumentCreated | None = None

    @model_validator(mode="after")
    def validate_exactly_one_result(self) -> "DraftCompletion":
        if (self.analysis is None) == (self.created is None):
            raise ValueError("exactly one completion result must be populated")
        return self


class DownloadDescriptor(BaseModel):
    document_id: int
    filename: str
    media_type: str
    path: Path
