from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


class Party(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    address: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "email", "address")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class InvoiceItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        return value.strip()


class InvoiceCreate(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=100)
    issue_date: date
    due_date: date | None = None
    currency: str = Field(min_length=3, max_length=3)
    business: Party
    client: Party
    items: list[InvoiceItemCreate] = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)
    payment_terms: str | None = Field(default=None, max_length=2000)

    @field_validator("invoice_number", "notes", "payment_terms")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("currency")
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

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be empty")
        return stripped


class AiTestResponse(BaseModel):
    answer: str
