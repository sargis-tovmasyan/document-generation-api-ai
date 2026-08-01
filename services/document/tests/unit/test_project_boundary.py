import ast
import os
from pathlib import Path
import subprocess
import sys


DOCUMENT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[4]


def test_document_service_owns_only_document_source() -> None:
    assert not (BACKEND_ROOT / "app").exists()
    for source_path in (DOCUMENT_ROOT / "src" / "document_service").rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        assert not any(
            blocked in module
            for module in imported_modules
            for blocked in ("chat_service", "chat-service")
        ), f"{source_path} crosses the service boundary: {imported_modules}"


def test_document_service_startup_does_not_create_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "documents.db"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [
                    str(DOCUMENT_ROOT / "src"),
                    str(BACKEND_ROOT / "packages" / "contracts" / "src"),
                ]
            ),
            "DOCUMENT_DATABASE_PATH": str(database_path),
            "DOCUMENT_GENERATED_DIR": str(tmp_path / "generated"),
        }
    )
    program = """
import sqlite3
from fastapi.testclient import TestClient
from document_service.main import app

with TestClient(app):
    pass
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
    assert result.stdout.splitlines() == ["Document Service", ""]
