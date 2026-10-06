"""Independent review repro: dependency failures must stay retryable."""
import pytest
from pydantic import ValidationError

import test_conversation as component
from test_conversation import NOW, RECEIVED, query, router
from budget_bot.ai.schemas import Query
from budget_bot.domain.errors import BudgetError
from budget_bot.workflows import BudgetWorkflow

store = component.store
owner = component.owner


@pytest.mark.parametrize('boundary', ['snapshot', 'spending', 'balances', 'day'])
@pytest.mark.parametrize('failure_kind', ['value', 'validation', 'invalid_date'])
async def test_dependency_value_error_propagates_instead_of_invalid_user_reply(
        store, owner, monkeypatch, boundary, failure_kind):
    route = router(store, BudgetWorkflow(store))
    before, pending = store.get_snapshot(owner), store.get_pending(owner)
    if failure_kind == 'validation':
        with pytest.raises(ValidationError) as invalid:
            Query.model_validate(query(report='invalid-dependency-report'))
        failure = invalid.value
    elif failure_kind == 'invalid_date':
        failure = BudgetError('invalid_date', 'Synthetic internal dependency failure')
    else:
        failure = ValueError('Synthetic internal dependency failure')
    calls = []

    def broken(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure

    with monkeypatch.context() as patch:
        if boundary == 'snapshot':
            patch.setattr(store, 'get_snapshot', broken)
        elif boundary == 'day':
            patch.setattr(store, 'spending', broken)
        else:
            patch.setattr(route.reports, boundary, broken)
        arguments = {'callback_data': 'day:2024-03-01'} if boundary == 'day' else {
            'query': query('balances' if boundary == 'balances' else 'spending')}
        with pytest.raises(type(failure)) as error:
            await route.dispatch(owner, None, RECEIVED, NOW, **arguments)
        assert error.value is failure
    assert len(calls) == 1 and calls[0][0][0] == owner
    assert store.get_snapshot(owner) == before and store.get_pending(owner) == pending


@pytest.mark.parametrize('page', [1, 999999])
async def test_out_of_range_calendar_page_is_safe_after_one_owner_read(store, owner, monkeypatch, page):
    route = router(store, BudgetWorkflow(store))
    before, pending = store.get_snapshot(owner), store.get_pending(owner)
    spending = store.spending
    calls = []

    def count_read(*args, **kwargs):
        calls.append((args, kwargs))
        return spending(*args, **kwargs)

    monkeypatch.setattr(store, 'spending', count_read)
    out = await route.dispatch(owner, None, RECEIVED, NOW, callback_data=f'day:2024-03-01:{page}')
    assert out == {'text': 'That calendar date or page is invalid. Open /calendar again.', 'keyboard': []}
    assert len(calls) == 1 and calls[0][0][0] == owner
    assert store.get_snapshot(owner) == before and store.get_pending(owner) == pending
