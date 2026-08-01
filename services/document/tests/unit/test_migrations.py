import os
import sqlite3
import subprocess
import sys
import shutil
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {"alembic_version", "invoices", "invoice_items"}


def upgrade(database: Path, config: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DOCUMENT_DATABASE_PATH"] = str(database)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(config or SERVICE_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=SERVICE_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def table_names(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_document_migration_upgrades_empty_and_existing_database_idempotently(tmp_path: Path) -> None:
    database = tmp_path / "documents.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE preserved (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO preserved VALUES ('keep-me')")

    first = upgrade(database)
    second = upgrade(database)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert EXPECTED_TABLES <= table_names(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id FROM preserved").fetchone() == ("keep-me",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001_document_baseline",)


def test_failed_document_upgrade_does_not_advance_version(tmp_path: Path) -> None:
    database = tmp_path / "documents.db"
    assert upgrade(database).returncode == 0
    migration_root = tmp_path / "migration-copy"
    shutil.copytree(SERVICE_ROOT / "migrations", migration_root / "migrations")
    shutil.copy2(SERVICE_ROOT / "alembic.ini", migration_root / "alembic.ini")
    (migration_root / "migrations" / "versions" / "0002_fail.py").write_text(
        "from alembic import op\nrevision='0002_fail'\ndown_revision='0001_document_baseline'\n"
        "branch_labels=None\ndepends_on=None\ndef upgrade():\n    op.execute('CREATE TABLE partial_document(id INTEGER)')\n    raise RuntimeError('expected')\n"
        "def downgrade():\n    pass\n",
        encoding="utf-8",
    )

    failed = upgrade(database, migration_root / "alembic.ini")

    assert failed.returncode != 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001_document_baseline",)
