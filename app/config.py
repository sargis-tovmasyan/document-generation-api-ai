from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = BASE_DIR / "generated"
INVOICE_PDF_DIR = GENERATED_DIR / "invoices"
TEMPLATES_DIR = BASE_DIR / "templates"
DATABASE_PATH = DATA_DIR / "app.db"


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INVOICE_PDF_DIR.mkdir(parents=True, exist_ok=True)
