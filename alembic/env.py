"""Alembic environment (async, asyncpg) — reads ``DATABASE_URL`` from ``Settings``.

Based on the official ``async`` template. Import every module that defines ORM
tables below so ``Base.metadata`` is complete for ``--autogenerate`` / ``alembic check``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from src.core.config import Settings
from src.core.database import Base
from src.modules.auth.infrastructure import orm as _auth_orm  # noqa: F401
from src.modules.social_connect.infrastructure import orm as _social_connect_orm  # noqa: F401
from src.modules.tenancy.infrastructure import orm as _tenancy_orm  # noqa: F401

config = context.config
# Programmatic callers (tests) pass ``config.attributes["configure_logger"] = False`` so
# ``fileConfig`` does not disable the application's loggers mid-session.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# ``-x database_url=...`` (tests) > DATABASE_URL env / .env (Settings).
_x_args = context.get_x_argument(as_dictionary=True)
database_url = _x_args.get("database_url") or Settings().database_url
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection (``alembic upgrade head --sql``)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
