from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

InvoiceTemplateLanguage = Literal["ru", "en"]


class Party(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    address: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "email", "address", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class InvoiceItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)

    @field_validator("description", mode="before")
    @classmethod
    def strip_description(cls, value: str) -> str:
        return value.strip()


class InvoiceCreate(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=100)
    issue_date: date
    due_date: date | None = None
    currency: str = Field(min_length=3, max_length=3)
    template_language: InvoiceTemplateLanguage = "ru"
    business: Party
    client: Party
    items: list[InvoiceItemCreate] = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)
    payment_terms: str | None = Field(default=None, max_length=2000)

    @field_validator("invoice_number", "notes", "payment_terms", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalpha():
            raise ValueError("currency must contain exactly three letters")
        return normalized

    @model_validator(mode="after")
    def validate_due_date(self) -> "InvoiceCreate":
        if self.due_date is not None and self.due_date < self.issue_date:
            raise ValueError("due_date must be on or after issue_date")
        return self


class InvoiceCreateResponse(BaseModel):
    id: int
    invoice_number: str
    total: Decimal
    pdf_url: str

    @field_serializer("total", when_used="json")
    def serialize_total(self, value: Decimal) -> float:
        return float(value)


class InvoiceListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    issue_date: date
    due_date: date | None
    currency: str
    business_name: str
    client_name: str
    total: Decimal
    pdf_url: str
    created_at: datetime

    @field_serializer("total", when_used="json")
    def serialize_total(self, value: Decimal) -> float:
        return float(value)


class AiTestRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message", mode="before")
    @classmethod
    def strip_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be empty")
        return stripped


class AiTestResponse(BaseModel):
    answer: str


class InvoiceDraftParty(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    address: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "email", "address", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class InvoiceDraftItem(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    quantity: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=4,
    )
    unit_price: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=14,
        decimal_places=2,
    )

    @field_validator("description", mode="before")
    @classmethod
    def strip_optional_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_serializer("quantity", "unit_price", when_used="json")
    def serialize_decimal(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None


class InvoiceDraft(BaseModel):
    document_type: Literal["invoice"] = "invoice"
    invoice_number: str | None = Field(default=None, max_length=100)
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, max_length=3)
    template_language: InvoiceTemplateLanguage | None = None
    business: InvoiceDraftParty = Field(default_factory=InvoiceDraftParty)
    client: InvoiceDraftParty = Field(default_factory=InvoiceDraftParty)
    items: list[InvoiceDraftItem] = Field(default_factory=list, max_length=100)
    raw_items: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=5000)
    payment_terms: str | None = Field(default=None, max_length=2000)

    @field_validator("invoice_number", "raw_items", "notes", "payment_terms", mode="before")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_optional_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not normalized:
            return None
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must contain exactly three letters")
        return normalized

    @model_validator(mode="after")
    def validate_due_date(self) -> "InvoiceDraft":
        if (
            self.issue_date is not None
            and self.due_date is not None
            and self.due_date < self.issue_date
        ):
            raise ValueError("due_date must be on or after issue_date")
        return self


class AiInvoiceExtractRequest(AiTestRequest):
    pass


class AiInvoiceExtractResponse(BaseModel):
    status: Literal["missing_fields", "ready"]
    draft: InvoiceDraft
    missing_fields: list[str]


class AiInvoiceErrorResponse(BaseModel):
    status: Literal["llm_unavailable", "ai_parse_error"]
    message: str


class AiChatRequest(AiTestRequest):
    pass


class AiChatAnswerResponse(BaseModel):
    status: Literal["answer"]
    message: str


class AiChatInvoiceListResponse(BaseModel):
    status: Literal["invoice_list"]
    message: str
    invoices: list[InvoiceListItem]


class AiChatMissingFieldsResponse(BaseModel):
    status: Literal["missing_fields"]
    missing_fields: list[str]
    draft: InvoiceDraft


class AiChatErrorResponse(BaseModel):
    status: Literal["llm_unavailable", "ai_parse_error"]
    message: str


class InvoiceDraftCompleteRequest(BaseModel):
    draft: InvoiceDraft
    chat_id: str | None = None


class InvoiceDraftMissingResponse(BaseModel):
    status: Literal["missing_fields"]
    missing_fields: list[str]


class InvoiceDraftCreatedResponse(BaseModel):
    status: Literal["created"]
    invoice_id: int
    invoice_number: str
    subtotal: Decimal
    total: Decimal
    currency: str
    pdf_url: str

    @field_serializer("subtotal", "total", when_used="json")
    def serialize_money(self, value: Decimal) -> float:
        return float(value)
