"""Workflow integration tests use only the coordinator-provided test database."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text

from budget_bot.domain.errors import BudgetError
from budget_bot.storage import BudgetStore
from budget_bot.workflows import BudgetWorkflow, postgres_checkpointer

NOW = datetime(2026, 10, 6, 18, 35, tzinfo=timezone.utc)


@pytest.fixture
def store():
    url = os.environ.get('BUDGET_TEST_DATABASE_URL')
    assert url, 'Coordinator must supply the test database environment.'
    db = BudgetStore(url, schema='test_w6')
    db.initialize()
    from budget_bot.storage.models import Base
    with db.engine.begin() as conn:
        names = ', '.join('"test_w6"."' + t.name + '"' for t in Base.metadata.sorted_tables)
        conn.execute(text('TRUNCATE ' + names + ' RESTART IDENTITY CASCADE'))
    yield db
    db.close()


def onboard(store, telegram_id=6001):
    owner = store.ensure_admin(telegram_id)
    r = store.propose(owner, [
        {'type': 'opening', 'amount_inr': '1000'},
        {'type': 'create_bucket', 'name': 'Travel'},
        {'type': 'allocate', 'bucket_name': 'Travel', 'amount_inr': '800'},
    ], NOW)
    store.confirm(owner, r['request_id'], r['revision'], NOW)
    return owner


def expense(amount='25', bucket='Travel'):
    a = {'type': 'expense', 'amount_inr': amount, 'description': 'Metro', 'date_expression': 'today'}
    if bucket is not None:
        a['bucket_name'] = bucket
    return a


async def test_real_interrupt_confirm_and_stable_ingress_replay(store):
    owner = onboard(store)
    saver = InMemorySaver()
    flow = BudgetWorkflow(store, checkpointer=saver)
    rid = str(uuid4())
    out = await flow.submit(owner, [expense()], NOW, request_id=rid)
    assert out['review']['request_id'] == rid
    assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 80000
    state = await flow.graph.aget_state(flow.config(owner, rid))
    assert state.next and state.tasks[0].interrupts
    assert await flow.submit(owner, [expense('999')], NOW, request_id=rid) == out
    reviewed = out['review']['plan']['events'][0]['transaction']
    flow = BudgetWorkflow(store, checkpointer=saver)
    done = await flow.decide(owner, rid, 1, 'confirm', NOW)
    assert done['result']['status'] == 'committed'
    assert done['result']['snapshot']['buckets']['Travel']['balance'] == 77500
    assert done['result']['snapshot']['transactions'][-1] == reviewed
    assert await flow.decide(owner, rid, 1, 'confirm', NOW + timedelta(days=1)) == done
    assert await flow.submit(owner, [expense()], NOW, request_id=rid) == done


async def test_stale_edit_cancel_callbacks_and_owner_binding(store):
    owner = onboard(store)
    other = store.ensure_admin(6002)
    flow = BudgetWorkflow(store)
    r = (await flow.submit(owner, [expense()], NOW))['review']
    with pytest.raises(BudgetError):
        await flow.decide(other, r['request_id'], 1, 'confirm', NOW)
    edited = await flow.decide(owner, r['request_id'], 1, 'edit', NOW, [expense('30')])
    assert edited['review']['revision'] == 2
    for decision in ('edit', 'cancel'):
        stale = await flow.decide(owner, r['request_id'], 1, decision, NOW, [expense('50')])
        assert stale['review']['revision'] == 2
        assert store.get_pending(owner)['revision'] == 2
    store.set_timezone(owner, 'Asia/Kolkata')
    stale = await flow.decide(owner, r['request_id'], 2, 'confirm', NOW)
    assert stale['review']['status'] == 'stale'
    assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 80000
    done = await flow.decide(owner, r['request_id'], stale['review']['revision'], 'confirm', NOW)
    assert done['result']['snapshot']['buckets']['Travel']['balance'] == 77000


async def test_missing_bucket_persisted_receipt_and_cancel(store):
    owner = onboard(store)
    saver = InMemorySaver()
    flow = BudgetWorkflow(store, checkpointer=saver)
    out = await flow.submit(owner, [expense(bucket=None)], NOW)
    assert out['review'] is None and 'Travel' in out['text']
    assert store.get_pending(owner) is None
    flow = BudgetWorkflow(store, checkpointer=saver)
    out = await flow.answer(owner, 'Travel', NOW + timedelta(minutes=10))
    assert out['review']['actions'][0]['date_expression'] == '2026-10-07'
    r = out['review']
    cancelled = await flow.decide(owner, r['request_id'], r['revision'], 'cancel', NOW)
    assert cancelled['result']['status'] == 'cancelled'
    assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 80000


class ControlledInterpreter:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    async def interpret(self, text, bucket_names, received_at, timezone, *, funding_source=None):
        self.calls.append((text, bucket_names, received_at, timezone))
        return next(self.outputs)


def interpreted(kind='mutation', actions=None, **kwargs):
    return {'schema_version': 1, 'kind': kind, 'actions': actions or [], 'missing_fields': [],
            'clarification_question': None, 'query': None, **kwargs}


async def test_funding_clarification_edit_and_validated_query(store):
    owner = onboard(store)
    ai = ControlledInterpreter([
        interpreted('clarification', clarification_question='Existing pool money or new income?',
                    missing_fields=['funding_source']),
        interpreted(actions=[{'type': 'income', 'amount_inr': '20', 'description': 'Gift'},
                             {'type': 'allocate', 'amount_inr': '20', 'bucket_name': 'Travel'}]),
        interpreted(actions=[expense('10')]),
        interpreted('query', query={'report': 'spending', 'period': 'today', 'start': None,
                                   'end': None, 'bucket_name': 'Travel'}),
    ])
    saver = InMemorySaver()
    flow = BudgetWorkflow(store, ai, saver)
    out = await flow.natural_language(owner, 'Add 20 to Travel', NOW)
    assert 'income' in out['text'] and store.get_pending(owner) is None
    flow = BudgetWorkflow(store, ai, saver)
    out = await flow.answer(owner, 'New income', NOW + timedelta(minutes=1))
    assert ai.calls[-1][2] == NOW
    assert 'Add 20 to Travel' in ai.calls[-1][0]
    r = out['review']
    edit = await flow.decide(owner, r['request_id'], 1, 'edit', NOW)
    assert edit['review'] is None
    out = await flow.answer(owner, 'Instead record metro 10 Travel', NOW)
    assert out['review']['revision'] == 2
    await flow.decide(owner, r['request_id'], 2, 'cancel', NOW)
    out = await flow.natural_language(owner, 'Spending today Travel', NOW)
    assert out['query']['report'] == 'spending' and out['result'] is None
    assert 'total' not in out['query']


async def test_nl_correction_ids_require_owner_selection(store):
    owner = onboard(store)
    r = store.propose(owner, [expense()], NOW)
    result = store.confirm(owner, r['request_id'], 1, NOW)
    tid = result['snapshot']['transactions'][-1]['id']
    ai = ControlledInterpreter([interpreted(actions=[{
        'type': 'correct', 'transaction_id': str(uuid4()), 'changes': {'amount_inr': '10'},
    }])])
    saver = InMemorySaver()
    flow = BudgetWorkflow(store, ai, saver)
    out = await flow.natural_language(owner, 'Correct metro to 10', NOW)
    assert out['review'] is None and 'Metro' in out['text']
    flow = BudgetWorkflow(store, ai, saver)
    out = await flow.answer(owner, tid, NOW)
    assert out['review']['actions'][0]['transaction_id'] == tid
    done = await flow.decide(owner, out['review']['request_id'], 1, 'confirm', NOW)
    assert done['result']['snapshot']['buckets']['Travel']['balance'] == 79000


async def test_post_commit_crash_recovers_without_double_write(store, monkeypatch):
    owner = onboard(store)
    saver = InMemorySaver()
    flow = BudgetWorkflow(store, checkpointer=saver)
    r = (await flow.submit(owner, [expense()], NOW))['review']
    confirm = store.confirm

    def crash(*args):
        confirm(*args)
        raise RuntimeError('Injected post-commit crash')

    monkeypatch.setattr(store, 'confirm', crash)
    with pytest.raises(RuntimeError, match='Injected'):
        await flow.decide(owner, r['request_id'], 1, 'confirm', NOW)
    monkeypatch.setattr(store, 'confirm', confirm)
    flow = BudgetWorkflow(store, checkpointer=saver)
    done = await flow.decide(owner, r['request_id'], 1, 'confirm', NOW)
    assert done['result']['snapshot']['buckets']['Travel']['balance'] == 77500
    assert len(done['result']['snapshot']['transactions']) == 3


async def test_postgres_checkpoint_restart(store):
    owner = onboard(store)
    url = os.environ['BUDGET_TEST_DATABASE_URL']
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = BudgetWorkflow(store, checkpointer=saver)
        out = await flow.submit(owner, [expense(bucket=None)], NOW)
        assert out['review'] is None
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = BudgetWorkflow(store, checkpointer=saver)
        r = (await flow.answer(owner, 'Travel', NOW))['review']
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = BudgetWorkflow(store, checkpointer=saver)
        done = await flow.decide(owner, r['request_id'], 1, 'confirm', NOW)
        assert done['result']['status'] == 'committed'
        assert await flow.submit(owner, [expense()], NOW, request_id=r['request_id']) == done


async def test_retry_invalid_answer_and_reject_model_selected_marker(store):
    owner = onboard(store)
    flow = BudgetWorkflow(store)
    await flow.submit(owner, [expense(bucket=None)], NOW)
    invalid = await flow.answer(owner, 'Unknown', NOW)
    assert 'Select an existing bucket' in invalid['text']
    out = await flow.answer(owner, 'Travel', NOW)
    assert out['review']['actions'][0]['bucket_name'] == 'Travel'
    await flow.decide(owner, out['review']['request_id'], 1, 'cancel', NOW)
    ai = ControlledInterpreter([interpreted(actions=[{
        'type': 'undo', 'transaction_id': store.get_snapshot(owner)['transactions'][-1]['id'],
        '_selected': True,
    }])])
    flow = BudgetWorkflow(store, ai)
    with pytest.raises(BudgetError) as error:
        await flow.natural_language(owner, 'Undo', NOW)
    assert error.value.code == 'invalid_model_output'


async def test_post_commit_recovery_releases_session_for_next_request(store, monkeypatch):
    owner = onboard(store)
    saver = InMemorySaver()
    flow = BudgetWorkflow(store, checkpointer=saver)
    r = (await flow.submit(owner, [expense()], NOW))['review']
    confirm = store.confirm

    def crash(*args):
        confirm(*args)
        raise RuntimeError('Injected post-commit crash')

    monkeypatch.setattr(store, 'confirm', crash)
    with pytest.raises(RuntimeError):
        await flow.decide(owner, r['request_id'], 1, 'confirm', NOW)
    monkeypatch.setattr(store, 'confirm', confirm)
    flow = BudgetWorkflow(store, checkpointer=saver)
    await flow.decide(owner, r['request_id'], 1, 'confirm', NOW)
    new = await flow.submit(owner, [expense('5')], NOW)
    assert new['review']['request_id'] != r['request_id']


async def test_provider_outage_and_no_interpreter_commands_still_work(store):
    owner = onboard(store)

    class Outage:
        async def interpret(self, *args):
            raise BudgetError('provider_unavailable', 'Natural-language interpretation is unavailable.')

    flow = BudgetWorkflow(store, Outage())
    out = await flow.natural_language(owner, 'Metro 25 Travel', NOW)
    assert 'unavailable' in out['text'] and out['review'] is None
    r = (await flow.submit(owner, [expense()], NOW))['review']
    assert (await flow.decide(owner, r['request_id'], 1, 'confirm', NOW))['result']['status'] == 'committed'


async def test_ambiguous_funding_cannot_be_silently_allocated(store):
    owner = onboard(store)
    ai = ControlledInterpreter([interpreted(actions=[{
        'type': 'allocate', 'amount_inr': '20', 'bucket_name': 'Travel',
    }])])
    flow = BudgetWorkflow(store, ai)
    out = await flow.natural_language(owner, 'Add 20 to Travel', NOW)
    assert out['review'] is None
    assert 'pool' in out['text'] and 'income' in out['text']
    assert store.get_pending(owner) is None


async def test_expired_review_does_not_block_new_ingress(store):
    owner = onboard(store)
    flow = BudgetWorkflow(store)
    r = (await flow.submit(owner, [expense()], NOW))['review']
    out = await flow.submit(owner, [expense('5')], NOW + timedelta(minutes=31))
    assert out['review']['request_id'] != r['request_id']


async def test_new_bucket_creation_is_explicit_atomic_review(store):
    owner = store.ensure_admin(6001)
    opening = store.propose(owner, [{'type': 'opening', 'amount_inr': '0'}], NOW)
    store.confirm(owner, opening['request_id'], 1, NOW)
    flow = BudgetWorkflow(store)
    await flow.submit(owner, [expense(bucket=None)], NOW)
    out = await flow.answer(owner, 'create: Food', NOW)
    r = out['review']
    assert [a['type'] for a in r['actions']] == ['create_bucket', 'expense']
    assert not store.get_snapshot(owner)['buckets']
    done = await flow.decide(owner, r['request_id'], 1, 'confirm', NOW)
    assert done['result']['snapshot']['buckets']['Food']['balance'] == -2500
    assert done['result']['warnings']


async def test_source_shortage_stays_atomic_and_query_totals_rejected(store):
    owner = onboard(store)
    flow = BudgetWorkflow(store)
    before = store.get_snapshot(owner)
    with pytest.raises(BudgetError):
        await flow.submit(owner, [
            {'type': 'income', 'amount_inr': '1', 'description': 'Gift'},
            {'type': 'allocate', 'amount_inr': '999', 'bucket_name': 'Travel'},
        ], NOW)
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None
    ai = ControlledInterpreter([interpreted('query', query={
        'report': 'spending', 'period': 'today', 'start': None, 'end': None,
        'bucket_name': None, 'total': 999,
    })])
    with pytest.raises(BudgetError) as error:
        await BudgetWorkflow(store, ai).natural_language(owner, 'Spending today', NOW)
    assert error.value.code == 'invalid_model_output'


async def test_invalid_edit_can_be_replaced_without_stuck_graph(store):
    owner = onboard(store)
    flow = BudgetWorkflow(store)
    r = (await flow.submit(owner, [expense()], NOW))['review']
    bad = await flow.decide(owner, r['request_id'], 1, 'edit', NOW, [expense('bad')])
    assert bad['review']['revision'] == 1
    out = await flow.decide(owner, r['request_id'], 1, 'edit', NOW, [expense('10')])
    assert out['review']['revision'] == 2


async def test_concurrent_duplicate_confirm_and_advisory_lock_outside_ai_transaction(store):
    import asyncio

    owner = onboard(store)

    class CheckLock:
        async def interpret(self, *args):
            with store.engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                                         "AND pid <> pg_backend_pid() AND granted")).scalar_one() >= 1
                assert conn.execute(text("SELECT count(*) FROM pg_stat_activity WHERE "
                                         "state = 'idle in transaction' AND query LIKE '%pg_try_advisory_lock%'"
                                         )).scalar_one() == 0
            return interpreted(actions=[expense()])

    flow = BudgetWorkflow(store, CheckLock())
    r = (await flow.natural_language(owner, 'Metro 25 Travel', NOW))['review']
    results = await asyncio.gather(*(flow.decide(owner, r['request_id'], 1, 'confirm', NOW)
                                     for _ in range(3)))
    assert results[0] == results[1] == results[2]
    assert results[0]['result']['snapshot']['buckets']['Travel']['balance'] == 77500


async def test_postgres_edit_and_postcommit_crash_restart(store, monkeypatch):
    owner = onboard(store)
    url = os.environ['BUDGET_TEST_DATABASE_URL']
    ai = ControlledInterpreter([interpreted(actions=[expense('10')])])
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = BudgetWorkflow(store, ai, saver)
        r = (await flow.submit(owner, [expense()], NOW))['review']
        await flow.decide(owner, r['request_id'], 1, 'edit', NOW)
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = BudgetWorkflow(store, ai, saver)
        r = (await flow.answer(owner, 'Metro 10 Travel', NOW))['review']
        assert r['revision'] == 2
        confirm = store.confirm

        def crash(*args):
            confirm(*args)
            raise RuntimeError('Injected post-commit crash')

        monkeypatch.setattr(store, 'confirm', crash)
        with pytest.raises(RuntimeError):
            await flow.decide(owner, r['request_id'], 2, 'confirm', NOW)
        monkeypatch.setattr(store, 'confirm', confirm)
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = BudgetWorkflow(store, ai, saver)
        done = await flow.decide(owner, r['request_id'], 2, 'confirm', NOW)
        assert done['result']['snapshot']['buckets']['Travel']['balance'] == 79000
        assert (await flow.submit(owner, [expense('5')], NOW))['review']


async def test_missing_amount_description_clarify_one_field_at_a_time(store):
    owner = onboard(store)
    flow = BudgetWorkflow(store)
    out = await flow.submit(owner, [{'type': 'expense', 'bucket_name': 'Travel'}], NOW)
    assert out['review'] is None and 'amount' in out['text']
    out = await flow.answer(owner, '12.50', NOW)
    assert out['review'] is None and 'description' in out['text']
    out = await flow.answer(owner, 'Metro', NOW)
    assert out['review']['actions'][0]['amount_inr'] == '12.50'
    assert out['review']['actions'][0]['description'] == 'Metro'
