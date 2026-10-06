from datetime import datetime, timezone
import os
import pytest
from sqlalchemy import text
from budget_bot.storage import BudgetStore
from budget_bot.storage.models import Base

NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)


@pytest.fixture
def real_store():
    store = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_integration')
    store.initialize()
    with store.engine.begin() as connection:
        names = ', '.join('"test_integration"."' + table.name + '"' for table in Base.metadata.sorted_tables)
        connection.execute(text('TRUNCATE ' + names + ' RESTART IDENTITY CASCADE'))
    yield store
    store.close()


def test_actual_planner_review_confirm_expense_report_and_replay(real_store):
    owner = real_store.ensure_admin(1)
    review = real_store.propose(owner, [{'type': 'opening', 'amount_inr': '1000'},
        {'type': 'create_bucket', 'name': 'Travel'},
        {'type': 'allocate', 'amount_inr': '500', 'bucket_name': 'Travel'}], NOW)
    result = real_store.confirm(owner, review['request_id'], review['revision'], NOW)
    assert result['status'] == 'committed'
    assert result['snapshot']['pool'] == 50000
    assert result['snapshot']['buckets']['Travel']['balance'] == 50000
    expense = real_store.propose(owner, [{'type': 'expense', 'amount_inr': '400',
        'bucket_name': 'Travel', 'description': 'Metro'}], NOW)
    saved = real_store.confirm(owner, expense['request_id'], expense['revision'], NOW)
    assert saved['snapshot']['buckets']['Travel']['balance'] == 10000
    assert real_store.confirm(owner, expense['request_id'], expense['revision'], NOW) == saved
    report = real_store.spending(owner, NOW.date(), NOW.date())
    assert report['total'] == 40000
    assert report['count'] == 1


def test_actual_target_version_survives_confirmation_and_reopen(real_store):
    owner = real_store.ensure_admin(1)
    opening = real_store.propose(owner, [{'type': 'opening', 'amount_inr': '100'},
        {'type': 'create_bucket', 'name': 'Travel'}], NOW)
    real_store.confirm(owner, opening['request_id'], 1, NOW)
    review = real_store.propose(owner, [{'type': 'set_target', 'amount_inr': '20', 'bucket_name': 'Travel'}], NOW)
    saved = real_store.confirm(owner, review['request_id'], 1, NOW)
    assert saved['status'] == 'committed'
    assert real_store.get_snapshot(owner)['buckets']['Travel']['target'] == 2000
    assert real_store.get_snapshot(owner)['targets'][-1]['target'] == 2000
