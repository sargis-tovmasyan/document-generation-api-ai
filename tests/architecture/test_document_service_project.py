import os
from pathlib import Path
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DOCUMENT_ROOT = BACKEND_ROOT / "document-service"


def test_document_service_initializes_only_document_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "documents.db"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [str(DOCUMENT_ROOT), str(BACKEND_ROOT / "contracts" / "python")]
            ),
            "DOCUMENT_DATABASE_PATH": str(database_path),
            "DOCUMENT_GENERATED_DIR": str(tmp_path / "generated"),
        }
    )
    program = """
import sqlite3
from app.database import initialize_database
from app.main import app

initialize_database()
with sqlite3.connect(r'%s') as connection:
    tables = [row[0] for row in connection.execute(
        \"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%%' ORDER BY name\"
    )]
print(app.title)
print(','.join(tables))
""" % database_path

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=DOCUMENT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Document Service",
        "invoice_items,invoices",
    ]
