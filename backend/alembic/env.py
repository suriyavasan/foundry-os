"""Alembic environment: uses the app's Base metadata and settings URL."""

from __future__ import annotations

from logging.config import fileConfig

import app.models  # noqa: F401  (registers all models on Base.metadata)
from alembic import context
from app.core.config import get_settings, resolve_database_url
from app.db.base import Base
from app.db.session import build_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return resolve_database_url(explicit)
    settings = get_settings()
    settings.prepare_data_dir()
    return settings.resolved_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Foreign keys OFF during migrations: SQLite batch mode recreates tables.
    engine = build_engine(_database_url(), enforce_foreign_keys=False)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
