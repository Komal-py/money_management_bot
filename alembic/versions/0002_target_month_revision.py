"""Scope target revision uniqueness to the planner's effective month.

Existing audit rows and their revision numbers are preserved verbatim.
"""
from alembic import op
from sqlalchemy import inspect

revision = '0002_target_month_revision'
down_revision = '0001_budget_storage'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    constraints = inspect(connection).get_unique_constraints('target_versions')
    for constraint in constraints:
        if constraint['column_names'] == ['bucket_id', 'revision']:
            op.drop_constraint(constraint['name'], 'target_versions', type_='unique')
    if not any(c['column_names'] == ['bucket_id', 'effective_month', 'revision'] for c in constraints):
        op.create_unique_constraint('target_versions_month_revision_key', 'target_versions',
                                    ['bucket_id', 'effective_month', 'revision'])


def downgrade():
    # Refuse if month-local revisions collide; never renumber immutable audit history.
    op.create_unique_constraint('target_versions_bucket_id_revision_key', 'target_versions',
                                ['bucket_id', 'revision'])
    op.drop_constraint('target_versions_month_revision_key', 'target_versions', type_='unique')
