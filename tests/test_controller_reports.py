"""Controller reports obey the frozen complete-query and trusted-owner contract."""
import pytest

from test_controller import NOW, Store, Workflow, message


class ReportStore(Store):
    def get_snapshot(self, owner):
        assert owner == 'owner-1'
        return {'timezone': 'Asia/Kolkata', 'buckets': {}}


class Reports:
    def __init__(self):
        self.calls = []

    def balances(self, owner):
        self.calls.append(('balances', owner))
        return 'Pool: ₹100.00'

    def spending(self, owner, period, received, **kwargs):
        self.calls.append(('spending', owner, period, received, kwargs))
        return 'Spending: ₹20.00'


def report_query(report, period):
    return {'report': report, 'period': period, 'start': None,
            'end': None, 'bucket_name': None}


@pytest.mark.asyncio
async def test_balance_query_uses_trusted_owner_only():
    from budget_bot.controller import BudgetController
    def parser(*_):
        return {'kind': 'query', 'query': report_query('balances', 'today')}
    reports = Reports()
    store, workflow = ReportStore(), Workflow()
    controller = BudgetController(store, workflow, None, reports, None, command_parser=parser)
    output = await controller.handle(message(text='/balance'), NOW)
    assert output[0]['text'] == 'Pool: ₹100.00'
    assert reports.calls == [('balances', 'owner-1')]
    assert store.calls == [1] and workflow.calls == []


@pytest.mark.asyncio
async def test_spending_query_is_read_only():
    from budget_bot.controller import BudgetController
    workflow, reports, store = Workflow(), Reports(), ReportStore()
    def parser(*_):
        return {'kind': 'query', 'query': report_query('spending', 'month')}
    controller = BudgetController(store, workflow, None, reports, None, command_parser=parser)
    output = await controller.handle(message(text='/spending month'), NOW)
    assert output[0]['text'] == 'Spending: ₹20.00'
    assert reports.calls == [('spending', 'owner-1', 'month', NOW,
                              {'start': None, 'end': None, 'bucket_name': None})]
    assert store.calls == [1] and workflow.calls == []
