# Backup and Restore

Stop the stack or otherwise quiesce writes. Copy `services/chat/data/chat.db`, `services/document/data/documents.db`, and the complete `services/document/generated` tree. Store checksums with the backup.

To restore, stop the stack, replace each service's files from the same backup set, verify checksums, run both Alembic upgrades, and start the stack. Confirm message/thread/invoice counts and download representative PDFs before discarding the previous files.
