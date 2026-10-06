"""Contract discovery; never reads deployment configuration."""
from budget_bot.domain.errors import BudgetError


def test_real_domain_error_contract():
    error = BudgetError('example', 'Safe message')
    assert isinstance(error, ValueError)
    assert (error.code, error.message) == ('example', 'Safe message')
