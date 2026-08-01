#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3


CHAT_TABLES = (
    "chat_threads",
    "chat_messages",
    "session_memories",
    "user_ui_settings",
    "shared_memories",
    "skill_memories",
    "memory_events",
)
DOCUMENT_TABLES = ("invoices", "invoice_items")
MIGRATION_NAME = "split-monolith-v1"


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _copy_tables(source: Path, target: Path, tables: tuple[str, ...]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with _read_only_connection(source) as legacy, sqlite3.connect(target) as destination:
        destination.execute("PRAGMA foreign_keys = OFF")
        destination.execute(
            """
            CREATE TABLE IF NOT EXISTS _service_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        if destination.execute(
            "SELECT 1 FROM _service_migrations WHERE name = ?",
            (MIGRATION_NAME,),
        ).fetchone():
            return

        try:
            destination.execute("BEGIN")
            for table in tables:
                schema_row = legacy.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                if schema_row is None:
                    continue
                if destination.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone() is None:
                    destination.execute(schema_row[0])

                rows = legacy.execute(f'SELECT * FROM "{table}"').fetchall()
                if rows:
                    placeholders = ",".join("?" for _ in rows[0])
                    destination.executemany(
                        f'INSERT OR IGNORE INTO "{table}" VALUES ({placeholders})',
                        rows,
                    )
                source_count = legacy.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                target_count = destination.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                if target_count < source_count:
                    raise RuntimeError(
                        f"Could not verify {table}: source={source_count}, target={target_count}"
                    )

            destination.execute(
                "INSERT INTO _service_migrations (name) VALUES (?)",
                (MIGRATION_NAME,),
            )
            destination.commit()
        except Exception:
            destination.rollback()
            raise
        finally:
            destination.execute("PRAGMA foreign_keys = ON")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy the legacy monolith database into service-owned databases."
    )
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--chat", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    args = parser.parse_args()

    if not args.legacy.is_file():
        raise SystemExit(f"Legacy database does not exist: {args.legacy}")

    _copy_tables(args.legacy, args.chat, CHAT_TABLES)
    _copy_tables(args.legacy, args.documents, DOCUMENT_TABLES)
    print(f"Migrated {args.legacy} without modifying it.")


if __name__ == "__main__":
    main()
