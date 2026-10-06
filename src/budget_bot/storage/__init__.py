"""PostgreSQL budget persistence."""
__all__ = ['BudgetStore']


def __getattr__(name):
    # Model-only Alembic operations must not depend on the pure planner being installed.
    if name == 'BudgetStore':
        from budget_bot.storage.store import BudgetStore
        return BudgetStore
    raise AttributeError(name)
