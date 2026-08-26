import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the parent directory to the Python path so we can import our app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.settings import settings
from app.db.base import Base
from app.db.models.portfolio import Position, Transaction, InvestmentAmount  # noqa
from app.db.models.market import Sector, StockSymbol  # noqa
from app.db.models.financial import Company, Period, Statement, StatementItem, ItemValue  # noqa


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def get_url():
    return settings.database_url


def include_object(object_, name, type_, reflected, compare_to):
    """Keep autogenerate off tables this metadata does not own.

    The ORM now shares the MySQL database ``my_portfolio`` with stores that
    create their own tables on demand rather than through a migration —
    ``raw_wichart_report``, ``wichart_reports`` (``app/stores/``) and
    ``report_rag``, all bootstrapped by ``app/db/mysql.py``.

    Alembic reflects the whole schema, so without this filter every
    ``--autogenerate`` would see those tables, find them absent from
    ``Base.metadata``, and emit ``drop_table`` for each — destroying the report
    data. Reflected tables that no model declares are ignored instead.

    This only constrains autogenerate; explicit ``op.*`` calls in a revision are
    unaffected.
    """
    if type_ == "table" and reflected and name not in target_metadata.tables:
        return False
    return True


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
