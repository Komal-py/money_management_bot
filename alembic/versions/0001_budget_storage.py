"""Initial application-owned persistence and append-only audit guards.

Revision ID: 0001_budget_storage
Revises: none
"""
from alembic import op

from budget_bot.storage.models import Base, install_guards

revision = '0001_budget_storage'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    Base.metadata.create_all(connection)
    install_guards(connection)


def downgrade():
    # Deliberately drop only named application tables, never the containing schema.
    connection = op.get_bind()
    Base.metadata.drop_all(connection)
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS reject_history_mutation()')
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS guard_posting_source()')
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS guard_current_revision()')
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS guard_revision_chain()')
    connection.exec_driver_sql('DROP FUNCTION IF EXISTS guard_opening_state()')
