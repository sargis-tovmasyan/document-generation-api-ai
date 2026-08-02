# ADR 0004: SQLite per Service

Status: accepted.

Chat and Document own independent SQLite files and Alembic histories. Repositories continue to use explicit SQL; Alembic owns schema changes without introducing an ORM. Compose migration jobs must succeed before their service starts. Backups and restores operate per service.
