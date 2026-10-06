"""Real PostgreSQL storage tests; the planner double is deliberately test-local."""
import copy
import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


class TestBudgetError(ValueError):
    __test__ = False

    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def local_plan(snapshot, actions, received_at):
    """Small deterministic contract double, not production financial logic."""
    s = copy.deepcopy(snapshot)
    postings, events, metadata = [], [], []
    batch = str(uuid4())
    for a in actions:
        kind = a['type']
        if kind == 'create_bucket':
            name = a['name'].strip()
            if any(n.casefold() == name.casefold() for n in s['buckets']):
                raise TestBudgetError('duplicate_bucket', 'Bucket already exists.')
            s['buckets'][name] = {'id': str(uuid4()), 'balance': 0, 'target': None}
            metadata.append({'type': kind, 'name': name})
            continue
        if kind == 'set_target':
            name = a['bucket_name']
            value = None if a.get('remove') else int(a['amount_inr']) * 100
            s['buckets'][name]['target'] = value
            metadata.append({'type': kind, 'bucket_name': name, 'target': value})
            continue
        previous = None
        if kind in ('undo', 'correct'):
            old = next(t for t in s['transactions'] if t['id'] == a['transaction_id'])
            previous = copy.deepcopy(old)
            if not old['active']:
                raise TestBudgetError('inactive_transaction', 'Transaction is inactive.')
            original = effects(old)
            for account, delta in original:
                apply(s, account, -delta, old['id'], postings, False)
            t = copy.deepcopy(old)
            t['revision'] += 1
            if kind == 'undo':
                t['active'] = False
            else:
                changes = a['changes']
                if 'amount_inr' in changes:
                    t['amount'] = int(changes['amount_inr']) * 100
                if 'date_expression' in changes:
                    t['date'] = changes['date_expression']
                if 'bucket_name' in changes:
                    t['bucket'] = changes['bucket_name']
                for account, delta in effects(t):
                    apply(s, account, delta, t['id'], postings, t['type'] == 'expense')
            s['transactions'][s['transactions'].index(old)] = t
        else:
            amount = int(a['amount_inr']) * 100
            if kind == 'opening':
                if s['onboarded']:
                    raise TestBudgetError('already_onboarded', 'Opening already recorded.')
                s['onboarded'] = True
                s['opening_date'] = received_at.date().isoformat()
            t = {'id': str(uuid4()), 'batch_id': batch, 'type': kind, 'amount': amount,
                 'bucket': a.get('bucket_name'), 'source': a.get('source_bucket'),
                 'destination': a.get('destination_bucket'), 'description': a.get('description', kind),
                 'date': a.get('date_expression') or received_at.date().isoformat(),
                 'active': True, 'revision': 1}
            if kind == 'expense' and date.fromisoformat(t['date']) > received_at.date():
                raise TestBudgetError('future_date', 'Future expenses are invalid.')
            for account, delta in effects(t):
                apply(s, account, delta, t['id'], postings, kind == 'expense')
            s['transactions'].append(t)
        events.append({'transaction': t, 'previous': previous})
    return {'snapshot': s, 'postings': postings, 'events': events, 'metadata': metadata,
            'warnings': [], 'actions': copy.deepcopy(actions), 'summary': ['Reviewed budget changes.']}


def effects(t):
    kind, amount = t['type'], t['amount']
    if kind in ('opening', 'income'):
        return [('pool', amount)]
    if kind == 'allocate':
        return [('pool', -amount), (t['bucket'], amount)]
    if kind == 'transfer':
        return [(t['source'], -amount), (t['destination'], amount)]
    if kind == 'expense':
        return [(t['bucket'], -amount)]
    raise AssertionError(kind)


def apply(s, account, delta, transaction_id, postings, expense):
    balance = s['pool'] if account == 'pool' else s['buckets'][account]['balance']
    if delta < 0 and balance + delta < 0 and not expense:
        raise TestBudgetError('insufficient_funds', 'Restore source funds first.')
    if account == 'pool':
        s['pool'] += delta
    else:
        s['buckets'][account]['balance'] += delta
    postings.append({'account': account, 'amount': delta, 'transaction_id': transaction_id})


# Only fill the missing W1 modules in this isolated worker test process.
try:
    import budget_bot.domain.errors  # noqa: F401
except ModuleNotFoundError:
    domain = types.ModuleType('budget_bot.domain')
    domain.__path__ = []
    errors = types.ModuleType('budget_bot.domain.errors')
    errors.BudgetError = TestBudgetError
    planner = types.ModuleType('budget_bot.domain.planner')
    planner.plan = local_plan
    money = types.ModuleType('budget_bot.domain.money')
    money.parse_money = lambda value: int(value) * 100
    sys.modules.update({'budget_bot.domain': domain, 'budget_bot.domain.errors': errors,
                        'budget_bot.domain.planner': planner, 'budget_bot.domain.money': money})

from budget_bot.storage import BudgetStore  # noqa: E402
from budget_bot.domain.errors import BudgetError  # noqa: E402

NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch):
    url = os.environ.get('BUDGET_TEST_DATABASE_URL')
    assert url, 'BUDGET_TEST_DATABASE_URL must be supplied; no credentials are read by these tests.'
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w2', 'Only test_w2 is authorized.'
    monkeypatch.setattr('budget_bot.storage.store.plan', local_plan)
    db = BudgetStore(url, schema='test_w2')
    db.initialize()
    from budget_bot.storage.models import Base
    with db.engine.begin() as conn:
        names = ', '.join('"test_w2"."' + table.name + '"' for table in Base.metadata.sorted_tables)
        conn.execute(text('TRUNCATE ' + names + ' RESTART IDENTITY CASCADE'))
    yield db
    db.close()


def onboard(store, telegram_id=1):
    owner = store.ensure_admin(telegram_id)
    review = store.propose(owner, [{'type': 'opening', 'amount_inr': '100'},
                                  {'type': 'create_bucket', 'name': 'Travel'},
                                  {'type': 'allocate', 'amount_inr': '80', 'bucket_name': 'Travel'}], NOW)
    result = store.confirm(owner, review['request_id'], review['revision'], NOW)
    return owner, result


def test_opening_confirmation_and_duplicate(store):
    owner = store.ensure_admin(1)
    assert store.get_snapshot(owner)['pool'] == 0
    review = store.propose(owner, [{'type': 'opening', 'amount_inr': '100'}], NOW)
    assert not store.get_snapshot(owner)['onboarded']
    result = store.confirm(owner, review['request_id'], 1, NOW)
    assert result['snapshot']['pool'] == 10000
    assert store.confirm(owner, review['request_id'], 1, NOW + timedelta(days=1)) == result
    assert store.cancel(owner, review['request_id'], NOW) == result
    with pytest.raises(BudgetError):
        store.propose(owner, [{'type': 'opening', 'amount_inr': '1'}], NOW)


def test_access_invites_and_owner_isolation(store):
    admin, _ = onboard(store)
    code = store.create_invite(admin, NOW)
    other = store.redeem_invite(code, 2, NOW)
    assert store.get_snapshot(other)['pool'] == 0
    with pytest.raises(BudgetError):
        store.redeem_invite(code, 3, NOW)
    with pytest.raises(BudgetError):
        store.create_invite(other, NOW)
    review = store.propose(admin, [{'type': 'income', 'amount_inr': '2'}], NOW)
    with pytest.raises(BudgetError):
        store.confirm(other, review['request_id'], 1, NOW)
    assert all('pool' not in user and 'transactions' not in user for user in store.list_access(admin))
    store.revoke_user(admin, 2, NOW)
    with pytest.raises(BudgetError):
        store.get_snapshot(other)


def test_edit_cancel_stale_expiry_and_pending(store):
    owner, _ = onboard(store)
    r = store.propose(owner, [{'type': 'income', 'amount_inr': '1'}], NOW)
    with pytest.raises(BudgetError):
        store.propose(owner, [{'type': 'income', 'amount_inr': '2'}], NOW)
    r2 = store.edit(owner, r['request_id'], [{'type': 'income', 'amount_inr': '3'}], NOW)
    assert r2['revision'] == 2
    old = store.confirm(owner, r['request_id'], 1, NOW)
    assert old['status'] == 'stale'
    assert store.get_snapshot(owner)['pool'] == 2000
    store.set_timezone(owner, 'UTC')
    stale = store.confirm(owner, r['request_id'], old['revision'], NOW)
    assert stale['status'] == 'stale'
    assert stale['revision'] > old['revision']
    assert store.confirm(owner, r['request_id'], stale['revision'], NOW)['status'] == 'committed'
    r = store.propose(owner, [{'type': 'income', 'amount_inr': '1'}], NOW)
    assert store.cancel(owner, r['request_id'], NOW)['status'] == 'cancelled'
    assert store.get_pending(owner) is None
    r = store.propose(owner, [{'type': 'income', 'amount_inr': '1'}], NOW)
    with pytest.raises(BudgetError):
        store.confirm(owner, r['request_id'], 1, NOW + timedelta(minutes=31))


def test_expenses_corrections_undo_and_targets(store):
    owner, _ = onboard(store)
    actions = [{'type': 'expense', 'amount_inr': '90', 'bucket_name': 'Travel',
                'date_expression': '2026-10-01', 'description': 'Train'},
               {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '100'}]
    r = store.propose(owner, actions, NOW)
    result = store.confirm(owner, r['request_id'], 1, NOW)
    assert result['snapshot']['buckets']['Travel']['balance'] == -1000
    expense = result['snapshot']['transactions'][-1]
    r = store.propose(owner, [{'type': 'correct', 'transaction_id': expense['id'],
                              'changes': {'amount_inr': '50', 'date_expression': '2026-10-02'}}], NOW)
    store.confirm(owner, r['request_id'], 1, NOW)
    report = store.spending(owner, date(2026, 10, 1), date(2026, 10, 6))
    assert report['total'] == 5000 and report['count'] == 1
    assert store.spending(owner, date(2026, 10, 1), date(2026, 10, 1))['count'] == 0
    r = store.propose(owner, [{'type': 'undo', 'transaction_id': expense['id']}], NOW)
    store.confirm(owner, r['request_id'], 1, NOW)
    assert store.spending(owner, date(2026, 10, 1), date(2026, 10, 6))['count'] == 0
    with store.engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM transaction_revisions')).scalar_one() == 5
        assert c.execute(text('SELECT count(*) FROM target_versions')).scalar_one() == 1


def test_source_guard_and_atomic_batch(store):
    owner, _ = onboard(store)
    before = store.get_snapshot(owner)
    with pytest.raises(BudgetError):
        store.propose(owner, [{'type': 'income', 'amount_inr': '1'},
                             {'type': 'allocate', 'amount_inr': '999', 'bucket_name': 'Travel'}], NOW)
    assert store.get_snapshot(owner) == before
    opening = before['transactions'][0]
    with pytest.raises(BudgetError):
        store.propose(owner, [{'type': 'undo', 'transaction_id': opening['id']}], NOW)


def test_inbox_outbox_durability_and_fencing(store):
    assert store.save_update('bot', 40, {'update_id': 40}, NOW)
    assert not store.save_update('bot', 40, {'update_id': 40, 'different': True}, NOW)
    assert store.save_update('bot', 42, {'update_id': 42}, NOW)
    assert store.polling_offset('bot') == 41
    store.save_update('bot', 41, {'update_id': 41}, NOW)
    assert store.polling_offset('bot') == 43
    assert len(store.pending_updates()) == 3
    store.complete_update(40, [{'chat_id': 1, 'text': 'ok', 'keyboard': None}], NOW)
    store.complete_update(40, [{'chat_id': 1, 'text': 'must not duplicate', 'keyboard': None}], NOW)
    first = store.pending_outbox(NOW)
    assert len(first) == 1 and first[0]['text'] == 'ok'
    assert store.pending_outbox(NOW) == []
    newer = store.pending_outbox(NOW + timedelta(minutes=2))[0]
    assert newer['lease_token'] != first[0]['lease_token']
    assert not store.ack_outbox(first[0]['id'], first[0]['lease_token'], NOW + timedelta(minutes=2))
    assert store.fail_outbox(newer['id'], newer['lease_token'], NOW + timedelta(minutes=2))
    retry = store.pending_outbox(NOW + timedelta(minutes=3))[0]
    assert store.ack_outbox(retry['id'], retry['lease_token'], NOW + timedelta(minutes=3))
    assert store.pending_outbox(NOW + timedelta(days=1)) == []


def test_concurrent_confirmation_and_invite_redemption(store):
    owner, _ = onboard(store)
    r = store.propose(owner, [{'type': 'income', 'amount_inr': '5'}], NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.confirm(owner, r['request_id'], 1, NOW), range(4)))
    assert all(result == results[0] for result in results)
    assert store.get_snapshot(owner)['pool'] == 2500
    code = store.create_invite(owner, NOW)

    def redeem(tid):
        try:
            return store.redeem_invite(code, tid, NOW)
        except BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, [2, 3]))
    assert sum(result is not None for result in results) == 1


def test_real_constraint_guards_and_schema_isolation(store):
    owner, result = onboard(store)
    with store.engine.connect() as c:
        assert c.execute(text('SHOW search_path')).scalar_one() == 'test_w2'
        assert c.execute(text('SHOW lock_timeout')).scalar_one() == '5s'
        assert c.execute(text('SHOW statement_timeout')).scalar_one() == '15s'
    for sql in [
        "UPDATE ledger_postings SET amount = 0",
        "DELETE FROM transaction_revisions",
        "UPDATE committed_results SET result = '{}'::jsonb",
        "DELETE FROM proposals",
        "UPDATE accounts SET balance = -1 WHERE kind = 'pool'",
        "INSERT INTO accounts (id, owner_id, kind, name, normalized_name, balance) "
        "SELECT gen_random_uuid(), owner_id, 'bucket', 'travel', 'travel', 0 FROM accounts WHERE name = 'Travel'",
        "INSERT INTO logical_transactions (id, owner_id, originating_batch_id, current, sequence) "
        "SELECT gen_random_uuid(), owner_id, originating_batch_id, current, 99 FROM logical_transactions "
        "WHERE current->>'type' = 'opening'",
    ]:
        with pytest.raises(IntegrityError), store.engine.begin() as c:
            c.execute(text(sql))
    other = store.redeem_invite(store.create_invite(owner, NOW), 2, NOW)
    with pytest.raises(IntegrityError), store.engine.begin() as c:
        c.execute(text('INSERT INTO ledger_postings '
                       '(id,owner_id,batch_id,transaction_id,account_id,amount,created_at) '
                       'SELECT gen_random_uuid(), :other, batch_id, transaction_id,account_id,amount,created_at '
                       'FROM ledger_postings LIMIT 1'), {'other': other})
    assert store.get_snapshot(owner) == result['snapshot']


def test_commit_failure_rolls_back_every_write(store, monkeypatch):
    from budget_bot.storage.models import BatchResult
    from sqlalchemy import event

    owner, _ = onboard(store)
    before = store.get_snapshot(owner)
    r = store.propose(owner, [{'type': 'income', 'amount_inr': '1'},
                              {'type': 'expense', 'amount_inr': '2', 'bucket_name': 'Travel'}], NOW)

    def crash(*args):
        raise RuntimeError('Injected final result insert failure')

    event.listen(BatchResult, 'before_insert', crash)
    try:
        with pytest.raises(RuntimeError, match='Injected'):
            store.confirm(owner, r['request_id'], 1, NOW)
    finally:
        event.remove(BatchResult, 'before_insert', crash)
    assert store.get_snapshot(owner) == before
    assert store.get_pending(owner)['status'] == 'pending'
    assert store.confirm(owner, r['request_id'], 1, NOW)['status'] == 'committed'


def test_review_keeps_initial_ids_and_detects_revalidation_change(store, monkeypatch):
    owner, _ = onboard(store)
    r = store.propose(owner, [{'type': 'income', 'amount_inr': '1'}], NOW)
    financial_id = r['plan']['events'][0]['transaction']['id']
    initial_plan = local_plan

    def changed_plan(snapshot, actions, received_at):
        p = initial_plan(snapshot, actions, received_at)
        p['warnings'] = ['New warning must be reviewed.']
        return p

    monkeypatch.setattr('budget_bot.storage.store.plan', changed_plan)
    stale = store.confirm(owner, r['request_id'], 1, NOW)
    assert stale['status'] == 'stale'
    assert store.get_snapshot(owner)['pool'] == 2000
    result = store.confirm(owner, r['request_id'], stale['revision'], NOW)
    assert result['snapshot']['transactions'][-1]['id'] == financial_id


def test_target_history_multiple_changes_and_removal(store):
    owner, _ = onboard(store)
    r = store.propose(owner, [
        {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '10'},
        {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '20'},
        {'type': 'set_target', 'bucket_name': 'Travel', 'remove': True}], NOW)
    result = store.confirm(owner, r['request_id'], 1, NOW)
    assert result['snapshot']['buckets']['Travel']['target'] is None
    assert [t['target'] for t in result['snapshot']['targets']] == [1000, 2000, None]
    for sql in ['UPDATE target_versions SET target = 1', 'DELETE FROM metadata_events']:
        with pytest.raises(IntegrityError), store.engine.begin() as c:
            c.execute(text(sql))


def test_alembic_upgrade_and_durable_reopen(store):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.attributes.update(database_url=os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w2')
    command.upgrade(config, 'head')
    with store.engine.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0001_budget_storage'
    owner, _ = onboard(store)
    r = store.propose(owner, [{'type': 'income', 'amount_inr': '5'}], NOW)
    store.save_update('bot', 1, {'update_id': 1}, NOW)
    store.complete_update(1, [{'chat_id': 1, 'text': 'durable', 'keyboard': [[{'text': 'Yes', 'data': 'yes'}]]}], NOW)
    reopened = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w2')
    try:
        assert reopened.get_pending(owner)['request_id'] == r['request_id']
        result = reopened.confirm(owner, r['request_id'], 1, NOW)
        assert reopened.confirm(owner, r['request_id'], 1, NOW) == result
        assert reopened.polling_offset('bot') == 2
        assert reopened.pending_outbox(NOW)[0]['text'] == 'durable'
    finally:
        reopened.close()


def test_review_batch_identity_and_repeated_transaction_events(store):
    owner, _ = onboard(store)
    r = store.propose(owner, [{'type': 'expense', 'amount_inr': '1', 'bucket_name': 'Travel'}], NOW)
    reviewed = r['plan']['events'][0]['transaction']
    result = store.confirm(owner, r['request_id'], 1, NOW)
    assert result['batch_id'] == reviewed['batch_id']
    assert result['snapshot']['transactions'][-1] == reviewed


def test_storage_rejects_unguarded_source_plan_atomically(store, monkeypatch):
    owner, _ = onboard(store)
    before = store.get_snapshot(owner)

    def unsafe_plan(snapshot, actions, received_at):
        p = local_plan(snapshot, [{'type': 'transfer', 'amount_inr': '1',
                                  'source_bucket': 'Travel', 'destination_bucket': 'Food'}], received_at)
        p['actions'] = copy.deepcopy(actions)
        for posting in p['postings']:
            posting['amount'] *= 100
        p['events'][0]['transaction']['amount'] *= 100
        p['snapshot']['transactions'][-1]['amount'] *= 100
        p['snapshot']['buckets']['Travel']['balance'] = -2000
        p['snapshot']['buckets']['Food']['balance'] = 10000
        return p

    r = store.propose(owner, [{'type': 'create_bucket', 'name': 'Food'}], NOW)
    store.confirm(owner, r['request_id'], 1, NOW)
    before = store.get_snapshot(owner)
    monkeypatch.setattr('budget_bot.storage.store.plan', unsafe_plan)
    r = store.propose(owner, [{'type': 'transfer', 'amount_inr': '100',
                              'source_bucket': 'Travel', 'destination_bucket': 'Food'}], NOW)
    with pytest.raises(BudgetError) as error:
        store.confirm(owner, r['request_id'], 1, NOW)
    assert error.value.code == 'insufficient_funds'
    assert store.get_snapshot(owner) == before
    assert store.get_pending(owner)['status'] == 'pending'


def test_unknown_or_unleased_outbox_ack_is_fenced(store):
    store.save_update('bot', 1, {'update_id': 1}, NOW)
    store.complete_update(1, [{'chat_id': 1, 'text': 'ok'}], NOW)
    with store.engine.connect() as c:
        outbox_id = c.execute(text('SELECT id FROM telegram_outbox')).scalar_one()
    assert not store.ack_outbox(str(outbox_id), None, NOW)
    assert not store.fail_outbox(str(outbox_id), None, NOW)
    assert len(store.pending_outbox(NOW)) == 1


def test_database_rejects_unfunded_posting_and_unaudited_current_state(store):
    owner, _ = onboard(store)
    r = store.propose(owner, [{'type': 'create_bucket', 'name': 'Food'},
                              {'type': 'transfer', 'amount_inr': '1', 'source_bucket': 'Travel',
                               'destination_bucket': 'Food'}], NOW)
    store.confirm(owner, r['request_id'], 1, NOW)
    for sql in [
        "INSERT INTO ledger_postings (id,owner_id,batch_id,transaction_id,account_id,amount,created_at) "
        "SELECT gen_random_uuid(),owner_id,batch_id,transaction_id,account_id,-999999,created_at "
        "FROM ledger_postings WHERE amount < 0 ORDER BY created_at LIMIT 1",
        "UPDATE logical_transactions SET current = jsonb_set(current, '{amount}', '99999')",
        "UPDATE logical_transactions SET current = jsonb_set(current, '{type}', '\"expense\"') "
        "WHERE current->>'type' = 'opening'",
        "UPDATE bot_users SET onboarded = false WHERE onboarded",
    ]:
        with pytest.raises(IntegrityError), store.engine.begin() as c:
            c.execute(text(sql))


def test_multiple_revisions_in_one_batch_and_current_reports(store):
    owner, _ = onboard(store)
    r = store.propose(owner, [{'type': 'expense', 'amount_inr': '1', 'bucket_name': 'Travel'}], NOW)
    result = store.confirm(owner, r['request_id'], 1, NOW)
    tid = result['snapshot']['transactions'][-1]['id']
    r = store.propose(owner, [
        {'type': 'correct', 'transaction_id': tid, 'changes': {'amount_inr': '2'}},
        {'type': 'correct', 'transaction_id': tid, 'changes': {'amount_inr': '3'}},
        {'type': 'undo', 'transaction_id': tid}], NOW)
    result = store.confirm(owner, r['request_id'], 1, NOW)
    assert result['snapshot']['transactions'][-1]['revision'] == 4
    assert result['snapshot']['buckets']['Travel']['balance'] == 8000
    assert store.calendar_day(owner, NOW.date())['total'] == 0
    with store.engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM transaction_revisions WHERE transaction_id = :tid'),
                         {'tid': tid}).scalar_one() == 4


def test_atomic_outbox_completion_failure_and_concurrent_claims(store):
    store.save_update('bot', 1, {'update_id': 1}, NOW)
    with pytest.raises(KeyError):
        store.complete_update(1, [{'chat_id': 1, 'text': 'must rollback'}, {'chat_id': 1}], NOW)
    assert len(store.pending_updates()) == 1
    assert store.pending_outbox(NOW) == []
    store.complete_update(1, [{'chat_id': 1, 'text': str(i)} for i in range(12)], NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.pending_outbox(NOW, limit=4), range(4)))
    ids = [row['id'] for claim in claims for row in claim]
    assert len(ids) == len(set(ids)) == 12


def test_clean_migration_roundtrip_own_tables_only(store):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.attributes.update(database_url=os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w2')
    command.upgrade(config, 'head')
    command.downgrade(config, 'base')
    with store.engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('test_w2.accounts')")).scalar_one() is None
        assert c.execute(text('SELECT current_schema()')).scalar_one() == 'test_w2'
    command.upgrade(config, 'head')
    owner, result = onboard(store)
    assert store.get_snapshot(owner) == result['snapshot']
