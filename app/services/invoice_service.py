import logging
import re
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from app.config import INVOICE_PDF_DIR
from app.database import database_connection
from app.observability_events import log_event, summarize_invoice_draft
from app.schemas import InvoiceCreate
from app.services.pdf_service import generate_invoice_pdf

MONEY_QUANTUM = Decimal("0.01")

logger = logging.getLogger(__name__)


class InvoiceNumberConflictError(Exception):
    """Raised when an invoice number already exists."""


def calculate_item_amount(quantity: Decimal, unit_price: Decimal) -> Decimal:
    return (quantity * unit_price).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def format_decimal(value: Decimal) -> str:
    return format(value, "f")


def build_pdf_filename(invoice_number: str) -> str:
    safe_number = re.sub(r"[^A-Za-z0-9_-]+", "-", invoice_number).strip("-_")
    safe_number = safe_number[:80] or "invoice"
    return f"{safe_number}-{uuid4().hex[:12]}.pdf"


def create_invoice(invoice: InvoiceCreate) -> dict:
    log_event(
        "invoice.service.create.started",
        invoice_number=invoice.invoice_number,
        draft=summarize_invoice_draft(invoice),
    )
    item_rows = [
        {
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "amount": calculate_item_amount(item.quantity, item.unit_price),
        }
        for item in invoice.items
    ]
    subtotal = sum((item["amount"] for item in item_rows), Decimal("0.00"))
    total = subtotal
    filename = build_pdf_filename(invoice.invoice_number)
    pdf_path = INVOICE_PDF_DIR / filename
    relative_pdf_path = Path("generated") / "invoices" / filename

    log_event(
        "invoice.service.calculated",
        invoice_number=invoice.invoice_number,
        item_count=len(item_rows),
        subtotal=subtotal,
        total=total,
        currency=invoice.currency,
        pdf_path=str(pdf_path),
    )

    template_context = {
        "invoice": invoice,
        "items": item_rows,
        "subtotal": subtotal,
        "total": total,
    }

    try:
        with database_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO invoices (
                    invoice_number, issue_date, due_date, currency,
                    business_name, business_email, business_address,
                    client_name, client_email, client_address,
                    subtotal, total, notes, payment_terms, pdf_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice.invoice_number,
                    invoice.issue_date.isoformat(),
                    invoice.due_date.isoformat() if invoice.due_date else None,
                    invoice.currency,
                    invoice.business.name,
                    invoice.business.email,
                    invoice.business.address,
                    invoice.client.name,
                    invoice.client.email,
                    invoice.client.address,
                    format_decimal(subtotal),
                    format_decimal(total),
                    invoice.notes,
                    invoice.payment_terms,
                    str(relative_pdf_path),
                ),
            )
            invoice_id = cursor.lastrowid
            log_event(
                "invoice.database.saved",
                invoice_id=invoice_id,
                invoice_number=invoice.invoice_number,
                item_count=len(item_rows),
            )

            connection.executemany(
                """
                INSERT INTO invoice_items (
                    invoice_id, description, quantity, unit_price, amount
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        invoice_id,
                        item["description"],
                        format_decimal(item["quantity"]),
                        format_decimal(item["unit_price"]),
                        format_decimal(item["amount"]),
                    )
                    for item in item_rows
                ],
            )
            log_event(
                "invoice.database.items_saved",
                invoice_id=invoice_id,
                invoice_number=invoice.invoice_number,
                item_count=len(item_rows),
            )

            generate_invoice_pdf(
                template_context,
                pdf_path,
                invoice.template_language,
            )
            log_event(
                "invoice.pdf.generated",
                invoice_id=invoice_id,
                invoice_number=invoice.invoice_number,
                template_language=invoice.template_language,
                pdf_path=str(pdf_path),
                pdf_url=f"/{relative_pdf_path}",
            )
    except sqlite3.IntegrityError as error:
        if pdf_path.exists():
            pdf_path.unlink()
        log_event(
            "invoice.service.create.conflict",
            level=logging.WARNING,
            invoice_number=invoice.invoice_number,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise InvoiceNumberConflictError(
            f"Invoice number '{invoice.invoice_number}' already exists"
        ) from error
    except Exception as error:
        if pdf_path.exists():
            pdf_path.unlink()
        log_event(
            "invoice.service.create.failed",
            level=logging.ERROR,
            invoice_number=invoice.invoice_number,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise

    created_invoice = {
        "id": invoice_id,
        "invoice_number": invoice.invoice_number,
        "subtotal": subtotal,
        "total": total,
        "currency": invoice.currency,
        "pdf_url": f"/generated/invoices/{filename}",
    }
    log_event(
        "invoice.service.create.completed",
        invoice_id=invoice_id,
        invoice_number=invoice.invoice_number,
        subtotal=subtotal,
        total=total,
        currency=invoice.currency,
        pdf_url=created_invoice["pdf_url"],
    )
    return created_invoice


def list_invoices() -> list[dict]:
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id, invoice_number, issue_date, due_date, currency,
                business_name, client_name, total, pdf_path, created_at
            FROM invoices
            ORDER BY id DESC
            """
        ).fetchall()

    invoices = [
        {
            **dict(row),
            "pdf_url": f"/{row['pdf_path']}",
        }
        for row in rows
    ]
    log_event("invoice.service.list.completed", invoice_count=len(invoices))
    return invoices


def reset_invoice_store() -> dict:
    with database_connection() as connection:
        invoice_count = connection.execute(
            "SELECT COUNT(*) AS count FROM invoices"
        ).fetchone()["count"]
        item_count = connection.execute(
            "SELECT COUNT(*) AS count FROM invoice_items"
        ).fetchone()["count"]

        connection.execute("DELETE FROM invoice_items")
        connection.execute("DELETE FROM invoices")
        connection.execute(
            "DELETE FROM sqlite_sequence WHERE name IN (?, ?)",
            ("invoice_items", "invoices"),
        )

    result = {
        "deleted_invoices": invoice_count,
        "deleted_items": item_count,
    }
    log_event("invoice.service.reset.completed", level=logging.WARNING, **result)
    return result


def get_invoice_pdf_path(invoice_id: int) -> Path | None:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT pdf_path FROM invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()

    if row is None:
        log_event("invoice.service.pdf_path.not_found", invoice_id=invoice_id)
        return None

    candidate = (INVOICE_PDF_DIR.parent.parent / row["pdf_path"]).resolve()
    generated_root = INVOICE_PDF_DIR.parent.resolve()
    if not candidate.is_relative_to(generated_root):
        log_event(
            "invoice.service.pdf_path.invalid",
            level=logging.ERROR,
            invoice_id=invoice_id,
            stored_path=row["pdf_path"],
            resolved_path=str(candidate),
        )
        raise ValueError("Stored PDF path is outside the generated directory")
    log_event(
        "invoice.service.pdf_path.resolved",
        invoice_id=invoice_id,
        stored_path=row["pdf_path"],
        resolved_path=str(candidate),
    )
    return candidate
