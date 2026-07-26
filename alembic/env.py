import logging
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.config import _redact_url, get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

logging.getLogger("boli").setLevel(logging.INFO)

target_metadata = Base.metadata

settings = get_settings()

logger = logging.getLogger("boli.alembic")
logger.info("alembic: database_url=%s", _redact_url(settings.database_url))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    We pass ``settings.database_url`` directly to ``create_engine`` instead of
    going through ``engine_from_config``/``ConfigParser.set_main_option`` to
    avoid ConfigParser's ``%`` interpolation, which crashes when the database
    URL contains percent-encoded characters (common in Railway Postgres
    passwords).
    """
    connectable = create_engine(settings.database_url, poolclass=pool.NullPool)

    logger.info("alembic: connecting to database for online migration")
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=settings.database_url.startswith("sqlite"),
        )

        with context.begin_transaction():
            context.run_migrations()
    logger.info("alembic: online migration complete")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
