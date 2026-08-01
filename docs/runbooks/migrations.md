# Database Migrations

Stop application writers before manual migration or restore. Back up the database, set `CHAT_DATABASE_PATH` or `DOCUMENT_DATABASE_PATH`, and run the package-owned Alembic command from the root README. Repeating `upgrade head` is safe. Compose runs `chat-migrate` and `document-migrate` before service startup; inspect those one-shot containers when a service does not start.

Baseline revisions use idempotent table creation so an existing database can be enrolled without rewriting records. Never delete `alembic_version` or edit an applied revision.
