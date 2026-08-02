"""Baseline Document database schema."""

from alembic import op


revision = "0001_document_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT NOT NULL UNIQUE,
            issue_date TEXT NOT NULL,
            due_date TEXT,
            currency TEXT NOT NULL,
            business_name TEXT NOT NULL,
            business_email TEXT,
            business_address TEXT,
            client_name TEXT NOT NULL,
            client_email TEXT,
            client_address TEXT,
            subtotal TEXT NOT NULL,
            total TEXT NOT NULL,
            notes TEXT,
            payment_terms TEXT,
            pdf_path TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            quantity TEXT NOT NULL,
            unit_price TEXT NOT NULL,
            amount TEXT NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS invoice_items")
    op.execute("DROP TABLE IF EXISTS invoices")
