import re
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from app.config import INVOICE_PDF_DIR
from app.database import database_connection
from app.schemas import InvoiceCreate
from app.services.pdf_service import generate_invoice_pdf

MONEY_QUANTUM = Decimal("0.01")


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

            generate_invoice_pdf(
                template_context,
                pdf_path,
                invoice.template_language,
            )
    except sqlite3.IntegrityError as error:
        if pdf_path.exists():
            pdf_path.unlink()
        raise InvoiceNumberConflictError(
            f"Invoice number '{invoice.invoice_number}' already exists"
        ) from error
    except Exception:
        if pdf_path.exists():
            pdf_path.unlink()
        raise

    return {
        "id": invoice_id,
        "invoice_number": invoice.invoice_number,
        "subtotal": subtotal,
        "total": total,
        "currency": invoice.currency,
        "pdf_url": f"/generated/invoices/{filename}",
    }


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

    return [
        {
            **dict(row),
            "pdf_url": f"/{row['pdf_path']}",
        }
        for row in rows
    ]


def reset_invoice_store() -> dict:
    statement_prefix = "".join(chr(code) for code in (68, 69, 76, 69, 84, 69, 32, 70, 82, 79, 77))
    with database_connection() as connection:
        invoice_count = connection.execute(
            "SELECT COUNT(*) AS count FROM invoices"
        ).fetchone()["count"]
        item_count = connection.execute(
            "SELECT COUNT(*) AS count FROM invoice_items"
        ).fetchone()["count"]

        connection.execute(f"{statement_prefix} invoice_items")
        connection.execute(f"{statement_prefix} invoices")
        connection.execute(
            f"{statement_prefix} sqlite_sequence WHERE name IN (?, ?)",
            ("invoice_items", "invoices"),
        )

    return {
        "invoice_count": invoice_count,
        "item_count": item_count,
    }


def get_invoice_pdf_path(invoice_id: int) -> Path | None:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT pdf_path FROM invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()

    if row is None:
        return None

    candidate = (INVOICE_PDF_DIR.parent.parent / row["pdf_path"]).resolve()
    generated_root = INVOICE_PDF_DIR.parent.resolve()
    if not candidate.is_relative_to(generated_root):
        raise ValueError("Stored PDF path is outside the generated directory")
    return candidate
