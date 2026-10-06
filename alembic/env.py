"""Application schema migrations; no credential files or library checkpoint tables."""
import os
import re

from alembic import context
from sqlalchemy import create_engine, event, pool, text

from budget_bot.storage.models import Base

config = context.config
schema = config.attributes.get('schema') or os.environ.get('BUDGET_SCHEMA', 'budget')
if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', schema):
    raise ValueError('Invalid application schema identifier.')
url = config.attributes.get('database_url') or os.environ.get('BUDGET_DATABASE_URL')
if not url:
    raise ValueError('Supply BUDGET_DATABASE_URL or Config.attributes[database_url].')


def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    if engine.dialect.name != 'postgresql':
        raise ValueError('PostgreSQL migrations only.')

    @event.listens_for(engine, 'connect')
    def configure(connection, _):
        old = connection.autocommit
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{schema}"')
                cursor.execute("SET lock_timeout TO '5s'")
                cursor.execute("SET statement_timeout TO '15s'")
                cursor.execute("SET idle_in_transaction_session_timeout TO '20s'")
        finally:
            connection.autocommit = old

    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
                context.configure(connection=connection, target_metadata=Base.metadata,
                                  version_table_schema=schema, transactional_ddl=True)
                with context.begin_transaction():
                    context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise ValueError('Use online PostgreSQL migration with an explicit application schema.')
run_migrations_online()
