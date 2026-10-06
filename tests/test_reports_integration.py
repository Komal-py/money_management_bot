"""Real planner/BudgetStore checks, confined to the approved test_w7 schema."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from budget_bot.domain.errors import BudgetError
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.storage.models import Base
from budget_bot.telegram.calendar import day_view, month_view, parse_callback

pytestmark = pytest.mark.postgres


def receipt(month):
    return datetime(2024, month, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def db():
    assert os.environ.get('BUDGET_TEST_DATABASE_URL'), 'Test database environment is required.'
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w7', 'Only test_w7 is authorized.'
    store = None
    try:
        store = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w7')
        store.initialize()
        with store.engine.connect() as connection:
            assert connection.scalar(text('SHOW search_path')) == 'test_w7'
            assert connection.scalar(text('SHOW lock_timeout')) == '5s'
            assert connection.scalar(text('SHOW statement_timeout')) == '15s'
    except Exception:
        if store:
            store.close()
        pytest.fail('Isolated test_w7 database setup failed (connection details suppressed).', pytrace=False)
    try:
        yield store
    finally:
        store.close()


def commit(db, owner, actions, now):
    before = db.get_snapshot(owner)
    review = db.propose(owner, actions, now)
    assert db.get_snapshot(owner) == before  # proposal alone never changes money
    result = db.confirm(owner, review['request_id'], review['revision'], now)
    assert result['status'] == 'committed'
    assert db.confirm(owner, review['request_id'], review['revision'], now) == result
    return result['snapshot']


def owner(db):
    return db.ensure_admin(uuid4().int % (2**62))


def expense(amount, day, description, bucket='Travel'):
    return {'type': 'expense', 'amount_inr': amount, 'bucket_name': bucket,
            'date_expression': day, 'description': description}


def counts(db):
    with db.engine.connect() as connection:
        return {table.name: connection.scalar(select(func.count()).select_from(table))
                for table in Base.metadata.sorted_tables}


def test_real_calendar_corrections_undo_owner_separation_and_read_only(db):
    a = owner(db)
    b = db.redeem_invite(db.create_invite(a, receipt(3)), uuid4().int % (2**62), receipt(3))
    commit(db, a, [{'type': 'opening', 'amount_inr': '10'},
                   {'type': 'create_bucket', 'name': 'Travel'},
                   {'type': 'create_bucket', 'name': 'Food'},
                   {'type': 'allocate', 'bucket_name': 'Travel', 'amount_inr': '0.16'}], receipt(3))
    for start in (0, 8, 16):
        commit(db, a, [expense('0.01', '2024-03-01', f'Fictional-{i:02d}')
                       for i in range(start, min(start + 8, 17))], receipt(3))
    snapshot = commit(db, a, [expense('1.00', '2024-03-01', 'To-correct'),
                             expense('2.00', '2024-03-01', 'To-undo')], receipt(3))
    ids = {t['description']: t['id'] for t in snapshot['transactions']}
    snapshot = commit(db, a, [
        {'type': 'correct', 'transaction_id': ids['To-correct'],
         'changes': {'amount_inr': '0.02', 'bucket_name': 'Food',
                     'date_expression': '2024-02-29', 'description': 'Corrected-fiction'}},
        {'type': 'undo', 'transaction_id': ids['To-undo']}], receipt(3))
    commit(db, b, [{'type': 'opening', 'amount_inr': '1'},
                   {'type': 'create_bucket', 'name': 'Travel'},
                   expense('0.99', '2024-03-01', 'Other-owner-secret')], receipt(3))
    current = {t['id']: t for t in snapshot['transactions']}
    assert current[ids['To-correct']]['revision'] == 2
    assert current[ids['To-undo']]['active'] is False
    assert snapshot['buckets']['Travel']['balance'] == -1
    assert snapshot['buckets']['Food']['balance'] == -2
    before = counts(db)
    other_before = db.get_snapshot(b)
    service = ReportService(db)
    pages = [day_view(db, a, '2024-03-01', page) for page in range(3)]
    assert [v['text'].count(' | ') // 3 for v in pages] == [8, 8, 1]
    for view in pages:
        assert 'Total spent: ₹0.17' in view['text']
        assert 'Other-owner-secret' not in view['text']
        assert 'To-correct' not in view['text'] and 'To-undo' not in view['text']
    descriptions = [line.split(' | ')[-1] for v in pages for line in v['text'].splitlines() if ' | ' in line]
    assert sorted(descriptions) == [f'Fictional-{i:02d}' for i in range(17)]
    assert day_view(db, a, '2024-03-01') == pages[0]
    assert 'Total spent: ₹0.99' in day_view(db, b, '2024-03-01')['text']
    assert 'Corrected-fiction' in day_view(db, a, '2024-02-29')['text']
    assert 'Total spent: ₹0.02' in service.day(a, '2024-02-29')
    assert 'No active expenses' in service.day(a, '2024-03-02')
    assert 'Total spent: ₹0.17' in service.spending(a, 'month', receipt(3))
    assert 'Total spent: ₹0.19' in service.spending(
        a, 'week', datetime(2024, 3, 1, 12, tzinfo=timezone.utc))
    assert 'Total spent: ₹0.19' in service.spending(a, 'range', receipt(3), '2024-02-29', '2024-03-01')
    assert 'Total spent: ₹0.02' in service.spending(a, 'range', receipt(3), '2024-02-29', '2024-03-01', ' food ')
    assert 'Travel: -₹0.01' in service.balances(a)
    assert 'Total available: ₹9.81' in service.balances(a)
    for view in [*pages, month_view(2024, 2)]:
        for row in view['keyboard']:
            for button in row:
                data = button['data']
                assert a not in data and b not in data and len(data.encode()) <= 64
                parse_callback(data)
    with pytest.raises(ValueError):
        day_view(db, a, '2024-03-01', 3)
    assert db.get_snapshot(a) == snapshot
    assert db.get_snapshot(b) == other_before
    assert counts(db) == before


def test_real_target_history_historical_month_revision_carry_and_removal(db):
    a = owner(db)
    commit(db, a, [{'type': 'opening', 'amount_inr': '10'},
                   {'type': 'create_bucket', 'name': 'Travel'},
                   expense('0.03', '2024-01-01', 'Before-target')], receipt(1))
    commit(db, a, [{'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '0.01'},
                   {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '0.02'},
                   expense('0.02', '2024-02-01', 'At-target')], receipt(2))
    commit(db, a, [expense('0.03', '2024-03-01', 'Carried-target')], receipt(3))
    commit(db, a, [{'type': 'set_target', 'bucket_name': 'Travel', 'remove': True},
                   expense('0.04', '2024-04-01', 'Removed-target')], receipt(4))
    snapshot = commit(db, a, [{'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '0.01'}], receipt(5))
    history = snapshot['targets']
    assert [(v['bucket_name'], v['effective_month'], v['amount'], v['target'], v['revision'])
            for v in history] == [('Travel', '2024-02-01', 1, 1, 1),
                                  ('Travel', '2024-02-01', 2, 2, 2),
                                  ('Travel', '2024-04-01', None, None, 3),
                                  ('Travel', '2024-05-01', 1, 1, 4)]
    service = ReportService(db)
    before = counts(db)
    assert 'target' not in service.spending(a, 'month', receipt(1))
    assert 'target ₹0.02' in service.spending(a, 'month', receipt(2))
    assert 'target ₹0.02' in service.spending(a, 'month', receipt(3))
    assert 'target' not in service.spending(a, 'month', receipt(4))
    assert db.get_snapshot(a) == snapshot
    assert counts(db) == before


@pytest.mark.parametrize('amount', ['0.001', '0.009', '-0.01'])
def test_real_invalid_subpaise_and_negative_input_does_not_write(db, amount):
    a = owner(db)
    commit(db, a, [{'type': 'opening', 'amount_inr': '0'},
                   {'type': 'create_bucket', 'name': 'Travel'}], receipt(3))
    before, table_counts = db.get_snapshot(a), counts(db)
    with pytest.raises(BudgetError):
        db.propose(a, [expense(amount, '2024-03-01', 'Invalid-fiction')], receipt(3))
    assert db.get_snapshot(a) == before
    assert counts(db) == table_counts
