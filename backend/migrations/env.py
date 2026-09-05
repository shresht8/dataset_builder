"""Alembic environment. Pulls the DB URL and metadata from the app.

Importing groundline_api.models registers every table on Base.metadata so
autogenerate sees the full §3 data model.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from groundline_api import models  # noqa: F401  (registers models)
from groundline_api.config import settings
from groundline_api.db import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
