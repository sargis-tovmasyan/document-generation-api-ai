import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

database_path = Path(os.environ.get("DOCUMENT_DATABASE_PATH", Path(__file__).resolve().parents[1] / "data" / "documents.db"))
database_path.parent.mkdir(parents=True, exist_ok=True)
config.set_main_option(
    "sqlalchemy.url",
    URL.create("sqlite", database=str(database_path.resolve())).render_as_string(hide_password=False).replace("%", "%%"),
)


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
