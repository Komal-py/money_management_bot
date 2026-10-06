from copy import deepcopy
from datetime import date

import pytest

from budget_bot.telegram.calendar import day_view, month_view, parse_callback
from test_reports import StoreDouble


def buttons(view):
    return [button for row in view['keyboard'] for button in row]


def test_leap_month_monday_grid_and_navigation():
    view = month_view(2024, 2)
    assert view['text'] == 'February 2024'
    assert [b['text'] for b in view['keyboard'][0]] == ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    assert [b['text'] for b in view['keyboard'][1]][:4] == [' ', ' ', ' ', '1']
    assert {'text': '29', 'data': 'day:2024-02-29'} in buttons(view)
    assert 'day:2023-02-29' not in [b['data'] for b in buttons(month_view(2023, 2))]
    assert 'cal:2023-12' in [b['data'] for b in buttons(month_view(2024, 1))]
    assert 'cal:2025-01' in [b['data'] for b in buttons(month_view(2024, 12))]


@pytest.mark.parametrize(('year', 'month'), [(1, 1), (9999, 12)])
def test_extreme_navigation_stays_bounded(year, month):
    for button in buttons(month_view(year, month)):
        assert len(button['data'].encode()) <= 64
        parsed = parse_callback(button['data'])
        assert parsed['kind'] in {'month', 'day'}


@pytest.mark.parametrize('payload', [
    'cal:0000-01', 'cal:10000-01', 'cal:2024-13', 'cal:2024-1',
    'day:2023-02-29', 'day:2024-02-29:-1', 'day:2024-02-29:1:2',
    'day:2024-02-29:1000000', 'day:2024-02-29:01', 'cal:2024-02:owner',
    'day:2024-02-29:' + '1' * 100, None,
])
def test_invalid_callback(payload):
    with pytest.raises(ValueError):
        parse_callback(payload)


def test_callback_page_shapes():
    assert parse_callback('cal:2024-02') == {'kind': 'month', 'year': 2024, 'month': 2}
    assert parse_callback('day:2024-02-29') == {'kind': 'day', 'date': date(2024, 2, 29), 'page': 0}
    assert parse_callback('day:2024-02-29:2')['page'] == 2


def test_day_active_rows_pagination_totals_and_owner_read_only():
    store = StoreDouble()
    store.rows = [store.row(f'{i:02d}', '2024-03-01', 'Travel', 1, f'Expense-{i:02d}')
                  for i in reversed(range(17))]
    store.rows += [store.row('gone', '2024-03-01', 'Travel', 999, 'Undone', active=False)]
    before = deepcopy(store.rows)
    first = day_view(store, 'owner-b', '2024-03-01')
    assert 'Total spent: ₹0.17' in first['text']
    assert 'Travel: ₹0.17' in first['text']
    assert 'Page 1 of 3' in first['text']
    assert first['text'].count('Expense-') == 8
    assert 'Expense-00' in first['text'] and 'Expense-08' not in first['text']
    assert 'day:2024-03-01:1' in [b['data'] for b in buttons(first)]
    last = day_view(store, 'owner-b', date(2024, 3, 1), 2)
    assert last['text'].count('Expense-') == 1
    assert 'Expense-16' in last['text']
    assert 'day:2024-03-01:1' in [b['data'] for b in buttons(last)]
    assert 'cal:2024-03' in [b['data'] for b in buttons(last)]
    assert store.rows == before
    assert all(call[1] == 'owner-b' for call in store.calls)
    with pytest.raises(ValueError):
        day_view(store, 'owner-b', '2024-03-01', 3)


def test_empty_day_and_invalid_page():
    view = day_view(StoreDouble(), 'owner-a', '2024-02-29')
    assert 'No active expenses' in view['text'] and '₹0.00' in view['text']
    for page in [-1, True, 1.5, 1000000]:
        with pytest.raises(ValueError):
            day_view(StoreDouble(), 'owner-a', '2024-02-29', page)


@pytest.mark.parametrize(('year', 'month'), [(True, 1), (2024, True), (0, 1), (10000, 1), (2024, 0)])
def test_invalid_month(year, month):
    with pytest.raises(ValueError):
        month_view(year, month)
