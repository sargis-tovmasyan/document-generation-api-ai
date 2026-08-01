#!/usr/bin/env python3
"""Verify SQLite contents and generated files before removing old runtime paths."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path


def database_snapshot(path: Path) -> dict[str, tuple[int, list[tuple[object, ...]]]]:
    snapshot: dict[str, tuple[int, list[tuple[object, ...]]]] = {}
    with sqlite3.connect(path) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "AND name != 'alembic_version' ORDER BY name"
            )
        ]
        for table in tables:
            primary_key = [
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{table}")')
                if row[5]
            ]
            rows = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            keys = []
            if primary_key:
                columns = ", ".join(f'"{column}"' for column in primary_key)
                keys = connection.execute(
                    f'SELECT {columns} FROM "{table}" ORDER BY {columns}'
                ).fetchall()
            snapshot[table] = (rows, keys)
    return snapshot


def file_snapshot(path: Path) -> dict[str, str]:
    return {
        str(file.relative_to(path)): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", action="append", nargs=2, metavar=("OLD", "NEW"), default=[])
    parser.add_argument("--files", action="append", nargs=2, metavar=("OLD", "NEW"), default=[])
    args = parser.parse_args()

    for old, new in args.database:
        before = database_snapshot(Path(old))
        after = database_snapshot(Path(new))
        if before != after:
            raise SystemExit(f"Database mismatch: {old} != {new}")
        counts = {table: rows for table, (rows, _) in before.items()}
        print(f"database verified: {old} -> {new}: {counts}")

    for old, new in args.files:
        before = file_snapshot(Path(old))
        after = file_snapshot(Path(new))
        if before != after:
            raise SystemExit(f"File mismatch: {old} != {new}")
        print(f"files verified: {old} -> {new}: {len(before)} files")


if __name__ == "__main__":
    main()
