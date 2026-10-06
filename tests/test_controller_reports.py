from test_controller import NOW, Store, Workflow, message
import pytest


class Reports:
    def balances(self, owner):
        return 'Pool: ₹100.00'

    def spending(self, owner, period, received, **kwargs):
        return 'Spending: ₹20.00'


@pytest.mark.asyncio
async def test_balance_query_uses_trusted_owner_only():
    from budget_bot.controller import BudgetController
    def parser(*_):
        return {'kind': 'query', 'query': {'report': 'balances'}}
    controller = BudgetController(Store(), Workflow(), None, Reports(), None, command_parser=parser)
    output = await controller.handle(message(text='/balance'), NOW)
    assert output[0]['text'] == 'Pool: ₹100.00'


@pytest.mark.asyncio
async def test_spending_query_is_read_only():
    from budget_bot.controller import BudgetController
    workflow = Workflow()
    def parser(*_):
        return {'kind': 'query', 'query': {'report': 'spending', 'period': 'month'}}
    controller = BudgetController(Store(), workflow, None, Reports(), None, command_parser=parser)
    output = await controller.handle(message(text='/spending month'), NOW)
    assert output[0]['text'] == 'Spending: ₹20.00'
    assert workflow.calls == []
