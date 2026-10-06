"""Real domain planner + PostgreSQL evidence, separate from contract-double tests."""
from datetime import date, timedelta

import pytest
from sqlalchemy import event, text

from budget_bot.domain.errors import BudgetError
from budget_bot.domain.planner import plan as real_plan
from budget_bot.storage import BudgetStore
from budget_bot.storage.models import BatchResult, Outbox
from test_storage import NOW, onboard, store as store


@pytest.fixture
def real_store(store, monkeypatch):
    monkeypatch.setattr('budget_bot.storage.store.plan', real_plan)
    return store


def commit(db, owner, actions, now=NOW):
    review = db.propose(owner, actions, now)
    result = db.confirm(owner, review['request_id'], review['revision'], now)
    assert result['status'] == 'committed'
    return review, result


def test_real_repeated_corrections_then_undo_preserve_all_revisions(real_store):
    db = real_store
    owner, _ = onboard(db)
    review, result = commit(db, owner, [
        {'type': 'expense', 'amount_inr': '1', 'bucket_name': 'Travel', 'description': 'Metro'}])
    original = review['plan']['events'][0]['transaction']
    tid = original['id']
    assert result['snapshot']['transactions'][-1] == original
    _, result = commit(db, owner, [
        {'type': 'correct', 'transaction_id': tid, 'changes': {'amount_inr': '2'}},
        {'type': 'correct', 'transaction_id': tid, 'changes': {'amount_inr': '3'}},
        {'type': 'undo', 'transaction_id': tid}])
    assert result['snapshot']['buckets']['Travel']['balance'] == 8000
    assert result['snapshot']['transactions'][-1] == {**original, 'amount': 300, 'revision': 4, 'active': False}
    assert db.calendar_day(owner, NOW.date())['count'] == 0
    with db.engine.connect() as conn:
        rows = conn.execute(text('SELECT state, previous FROM transaction_revisions '
                                 'WHERE transaction_id = :tid ORDER BY revision'), {'tid': tid}).all()
    assert [row.state['revision'] for row in rows] == [1, 2, 3, 4]
    assert rows[0].state == original
    assert rows[0].previous is None
    assert all(rows[i].previous == rows[i - 1].state for i in range(1, 4))


def test_real_ordered_targets_keep_reviewed_ids_and_month_revisions(real_store):
    db = real_store
    owner, _ = onboard(db)
    review, result = commit(db, owner, [
        {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '10'},
        {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '20'},
        {'type': 'set_target', 'bucket_name': 'Travel', 'remove': True}])
    reviewed = review['plan']['snapshot']['targets']
    stored = result['snapshot']['targets']
    assert [t['amount'] for t in stored] == [1000, 2000, None]
    assert [t['id'] for t in stored] == [t['id'] for t in reviewed]
    assert [t['revision'] for t in stored] == [1, 2, 3]
    november = NOW.replace(month=11)
    review, result = commit(db, owner, [
        {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '30'}], november)
    latest = result['snapshot']['targets'][-1]
    assert latest['id'] == review['plan']['snapshot']['targets'][-1]['id']
    assert latest['revision'] == review['plan']['snapshot']['targets'][-1]['revision'] == 1
    assert latest['effective_month'] == '2026-11-01'


def test_real_corrected_batch_undo_uses_current_versions_and_source_guards(real_store):
    db = real_store
    owner, _ = onboard(db)
    _, result = commit(db, owner, [
        {'type': 'income', 'amount_inr': '10', 'description': 'Salary'},
        {'type': 'allocate', 'amount_inr': '10', 'bucket_name': 'Travel'}])
    batch_id = result['batch_id']
    allocation = result['snapshot']['transactions'][-1]
    commit(db, owner, [{'type': 'correct', 'transaction_id': allocation['id'],
                        'changes': {'amount_inr': '5'}}])
    _, result = commit(db, owner, [{'type': 'undo', 'batch_id': batch_id}])
    assert result['snapshot']['pool'] == 2000
    assert result['snapshot']['buckets']['Travel']['balance'] == 8000
    assert result['snapshot']['transactions'][-1]['revision'] == 3
    _, result = commit(db, owner, [
        {'type': 'allocate', 'amount_inr': '20', 'bucket_name': 'Travel'},
        {'type': 'expense', 'amount_inr': '100', 'bucket_name': 'Travel', 'description': 'Train'}])
    allocation_id = result['snapshot']['transactions'][-2]['id']
    before = db.get_snapshot(owner)
    for action in [
        {'type': 'undo', 'transaction_id': allocation_id},
        {'type': 'correct', 'transaction_id': allocation_id, 'changes': {'amount_inr': '10'}}]:
        with pytest.raises(BudgetError) as exc:
            db.propose(owner, [action], NOW)
        assert exc.value.code == 'insufficient_source_funds'
        assert db.get_snapshot(owner) == before
        assert db.get_pending(owner) is None


def test_real_stale_review_replay_owner_and_expiry(real_store):
    db = real_store
    owner, _ = onboard(db)
    other = db.redeem_invite(db.create_invite(owner, NOW), 2, NOW)
    review = db.propose(owner, [
        {'type': 'expense', 'amount_inr': '90', 'bucket_name': 'Travel',
         'description': 'Train', 'date_expression': '2026-10-01'}], NOW)
    assert any('negative balance' in w for w in review['plan']['warnings'])
    assert any('Historical expense' in w for w in review['plan']['warnings'])
    before = db.get_snapshot(owner)
    with pytest.raises(BudgetError) as exc:
        db.confirm(other, review['request_id'], 1, NOW)
    assert exc.value.code == 'not_found'
    edited = db.edit(owner, review['request_id'], [
        {'type': 'expense', 'amount_inr': '50', 'bucket_name': 'Travel', 'description': 'Train'}], NOW)
    stale = db.confirm(owner, review['request_id'], review['revision'], NOW)
    assert stale['status'] == 'stale'
    assert db.get_snapshot(owner) == before
    db.set_timezone(owner, 'UTC')
    stale = db.confirm(owner, review['request_id'], stale['revision'], NOW)
    assert stale['status'] == 'stale'
    assert stale['plan']['events'][0]['transaction']['id'] == edited['plan']['events'][0]['transaction']['id']
    result = db.confirm(owner, review['request_id'], stale['revision'], NOW)
    assert result['status'] == 'committed'
    assert db.confirm(owner, review['request_id'], 1, NOW + timedelta(days=1)) == result
    assert db.cancel(owner, review['request_id'], NOW) == result
    assert db.spending(owner, date(2026, 10, 6), date(2026, 10, 6))['total'] == 5000
    expired = db.propose(owner, [{'type': 'income', 'amount_inr': '1', 'description': 'Gift'}], NOW)
    before = db.get_snapshot(owner)
    with pytest.raises(BudgetError) as exc:
        db.confirm(owner, expired['request_id'], 1, NOW + timedelta(minutes=30))
    assert exc.value.code == 'review_expired'
    assert db.get_snapshot(owner) == before


def test_durable_offset_advances_over_telegram_id_gaps(real_store):
    db = real_store
    assert db.save_update('bot', 40, {'update_id': 40}, NOW)
    assert db.save_update('bot', 900, {'update_id': 900}, NOW)
    assert db.polling_offset('bot') == 901
    assert db.save_update('bot', 42, {'update_id': 42}, NOW)
    assert not db.save_update('bot', 900, {'changed': True}, NOW)
    assert db.polling_offset('bot') == 901
    reopened = BudgetStore(db.engine.url, schema='test_w2')
    try:
        assert reopened.polling_offset('bot') == 901
        assert len(reopened.pending_updates()) == 3
    finally:
        reopened.close()


def test_pending_inbox_and_completion_are_bot_scoped(real_store):
    db = real_store
    db.save_update('first', 7, {'text': 'first'}, NOW)
    db.save_update('second', 7, {'text': 'second'}, NOW)
    assert [r['payload'] for r in db.pending_updates(bot_id='first')] == [{'text': 'first'}]
    with pytest.raises(BudgetError) as exc:
        db.complete_update(7, [], NOW)
    assert exc.value.code == 'ambiguous_update'
    db.complete_update(7, [{'chat_id': 1, 'text': 'only first'}], NOW, bot_id='first')
    assert db.pending_updates(bot_id='first') == []
    assert len(db.pending_updates(bot_id='second')) == 1
    with db.engine.connect() as conn:
        assert conn.execute(text('SELECT bot_id FROM telegram_outbox')).scalar_one() == 'first'
    assert db.polling_offset('first') == db.polling_offset('second') == 8


def test_outbox_insert_failure_rolls_back_and_expired_tokens_are_fenced(real_store):
    db = real_store
    db.save_update('bot', 1, {'update_id': 1}, NOW)

    def crash(*args):
        raise RuntimeError('Injected outbox insert failure')

    event.listen(Outbox, 'before_insert', crash)
    try:
        with pytest.raises(RuntimeError, match='Injected outbox'):
            db.complete_update(1, [{'chat_id': 1, 'text': 'retry me'}], NOW)
    finally:
        event.remove(Outbox, 'before_insert', crash)
    assert len(db.pending_updates()) == 1
    assert db.pending_outbox(NOW) == []
    db.complete_update(1, [{'chat_id': 1, 'text': 'retry me'}], NOW)
    claim = db.pending_outbox(NOW)[0]
    expiry = NOW + timedelta(seconds=60)
    assert not db.ack_outbox(claim['id'], claim['lease_token'], expiry)
    assert not db.fail_outbox(claim['id'], claim['lease_token'], expiry)
    renewed = db.pending_outbox(expiry)[0]
    assert renewed['lease_token'] != claim['lease_token']
    assert not db.fail_outbox(claim['id'], claim['lease_token'], expiry)
    assert db.ack_outbox(renewed['id'], renewed['lease_token'], expiry)
    assert db.pending_outbox(expiry) == []


def test_real_correction_moves_current_report_date_and_bucket(real_store):
    db = real_store
    owner, _ = onboard(db)
    _, result = commit(db, owner, [
        {'type': 'create_bucket', 'name': 'Food'},
        {'type': 'expense', 'amount_inr': '90', 'bucket_name': 'Travel',
         'description': 'Train', 'date_expression': '2026-10-01'}])
    tid = result['snapshot']['transactions'][-1]['id']
    _, result = commit(db, owner, [{'type': 'correct', 'transaction_id': tid, 'changes': {
        'amount_inr': '50', 'bucket_name': 'Food', 'date_expression': '2026-10-02'}}])
    assert result['snapshot']['buckets']['Travel']['balance'] == 8000
    assert result['snapshot']['buckets']['Food']['balance'] == -5000
    assert db.calendar_day(owner, date(2026, 10, 1))['count'] == 0
    assert db.spending(owner, date(2026, 10, 1), NOW.date(), 'travel')['total'] == 0
    report = db.calendar_day(owner, date(2026, 10, 2))
    assert report['total'] == 5000 and report['by_bucket'] == {'Food': 5000}
    assert report['expenses'][0]['id'] == tid


def test_real_final_result_failure_rolls_back_ledger_metadata_and_request(real_store):
    db = real_store
    owner, _ = onboard(db)
    before = db.get_snapshot(owner)
    review = db.propose(owner, [
        {'type': 'create_bucket', 'name': 'Food'},
        {'type': 'set_target', 'bucket_name': 'Food', 'amount_inr': '5'},
        {'type': 'expense', 'bucket_name': 'Food', 'amount_inr': '5', 'description': 'Lunch'}], NOW)

    def counts():
        with db.engine.connect() as conn:
            return [conn.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() for table in (
                'financial_batches', 'accounts', 'logical_transactions', 'transaction_revisions',
                'ledger_postings', 'target_versions', 'metadata_events', 'committed_results')]

    initial_counts = counts()
    pending_before = db.get_pending(owner)

    def crash(*args):
        raise RuntimeError('Injected real-plan result failure')

    event.listen(BatchResult, 'before_insert', crash)
    try:
        with pytest.raises(RuntimeError, match='Injected real-plan'):
            db.confirm(owner, review['request_id'], 1, NOW)
    finally:
        event.remove(BatchResult, 'before_insert', crash)
    assert db.get_snapshot(owner) == before
    assert counts() == initial_counts
    assert db.get_pending(owner) == pending_before
    result = db.confirm(owner, review['request_id'], 1, NOW)
    assert result['status'] == 'committed'
    assert any('monthly target' in w for w in result['warnings'])


def test_inbox_insert_failure_does_not_acknowledge_offset(real_store):
    db = real_store
    db.save_update('bot', 1, {'update_id': 1}, NOW)

    def crash(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO telegram_inbox'):
            raise RuntimeError('Injected inbox insert failure')

    event.listen(db.engine, 'before_cursor_execute', crash)
    try:
        with pytest.raises(RuntimeError, match='Injected inbox'):
            db.save_update('bot', 900, {'update_id': 900}, NOW)
    finally:
        event.remove(db.engine, 'before_cursor_execute', crash)
    assert db.polling_offset('bot') == 2
    assert [r['update_id'] for r in db.pending_updates()] == [1]
    assert db.save_update('bot', 900, {'update_id': 900}, NOW)
    assert db.polling_offset('bot') == 901


def test_target_constraint_migration_preserves_existing_audit(real_store):
    import os
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import IntegrityError

    db = real_store
    owner, _ = onboard(db)
    commit(db, owner, [{'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '10'}])
    before = db.get_snapshot(owner)
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.attributes.update(database_url=os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w2')
    command.downgrade(config, '0001_budget_storage')
    command.upgrade(config, 'head')
    assert db.get_snapshot(owner) == before
    commit(db, owner, [{'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '20'}],
           NOW.replace(month=11))
    before = db.get_snapshot(owner)
    # Downgrade must not silently renumber immutable month-local audit revisions.
    with pytest.raises(IntegrityError):
        command.downgrade(config, '0001_budget_storage')
    assert db.get_snapshot(owner) == before
    with db.engine.connect() as conn:
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0002_target_month_revision'
