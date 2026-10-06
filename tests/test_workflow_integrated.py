"""Real cross-worker seams: PostgreSQL/LangGraph and controlled Responses wire."""
import json
import os
from uuid import uuid4

import httpx
import pytest

from budget_bot.ai import render_result, render_review
from budget_bot.services.onboarding import OnboardingService
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.workflows import BudgetWorkflow, postgres_checkpointer
from test_ai_provider import client_for, mutation, response
from test_workflow import NOW, expense, onboard


@pytest.fixture
def db():
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w6'
    database = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w6')
    database.initialize()
    yield database
    database.close()


def rendered_workflow(db, interpreter=None, saver=None):
    flow = BudgetWorkflow(db, interpreter, saver)
    flow.render_review = render_review
    flow.render_result = render_result
    return flow


async def test_setup_review_handoff_survives_postgres_graph_reopen(db):
    owner = db.ensure_admin(uuid4().int % (2**62))
    setup = OnboardingService(db)
    setup.initialize()
    initial = db.get_snapshot(owner)
    for text in ('/start', '100', 'Travel', '50', 'setup:finish'):
        output = setup.handle(owner, text, NOW)
    review = output['review']
    assert review and db.get_snapshot(owner) == initial
    rid = review['request_id']
    url = os.environ['BUDGET_TEST_DATABASE_URL']
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = rendered_workflow(db, saver=saver)
        handoff = await flow.submit(owner, review['actions'], NOW, request_id=rid)
        assert handoff['review']['request_id'] == rid
        assert handoff['review']['plan'] == review['plan']
        assert 'Pool: INR 50.00' in handoff['text']
        assert db.get_snapshot(owner) == initial
    async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
        flow = rendered_workflow(db, saver=saver)
        saved = await flow.decide(owner, rid, review['revision'], 'confirm', NOW)
        assert saved['result']['status'] == 'committed'
        assert saved['result']['snapshot']['pool'] == 5000
        assert saved['result']['snapshot']['buckets']['Travel']['balance'] == 5000
        assert saved['text'].startswith('Recorded.')
        assert await flow.decide(owner, rid, review['revision'], 'confirm', NOW) == saved
    assert setup.handle(owner, '/start', NOW)['done'] is True


async def test_real_ai_expense_and_query_match_real_workflow_and_reports(monkeypatch, db):
    owner = onboard(db, telegram_id=uuid4().int % (2**62))
    seen = []
    outputs = iter([
        mutation(expense('25')),
        {'schema_version': 1, 'kind': 'query', 'actions': [], 'missing_fields': [],
         'clarification_question': None, 'query': {'report': 'spending', 'period': 'today',
                                                  'start': None, 'end': None, 'bucket_name': 'Travel'}},
    ])

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=response(next(outputs)))

    client = client_for(monkeypatch, handler)
    try:
        flow = rendered_workflow(db, client)
        before = db.get_snapshot(owner)
        review = (await flow.natural_language(owner, 'Record Metro expense 25 in Travel', NOW))['review']
        assert review is not None
        assert db.get_snapshot(owner) == before
        result = await flow.decide(owner, review['request_id'], review['revision'], 'confirm', NOW)
        assert result['result']['snapshot']['buckets']['Travel']['balance'] == 77500
        validated = await flow.natural_language(owner, 'Travel spending today', NOW)
        query = validated['query']
        text = ReportService(db).spending(owner, query['period'], NOW, start=query['start'],
                                         end=query['end'], bucket_name=query['bucket_name'])
        assert '25.00' in text
        for request in seen:
            wire = json.dumps(request['input'])
            assert owner not in wire and 'owner_id' not in wire and 'balance' not in wire
            assert request['store'] is False
    finally:
        await client.close()


@pytest.mark.parametrize('draft', [
    {'type': 'undo', 'last': True, 'reference': None},
    {'type': 'correct', 'reference': 'Metro', 'changes': {'amount_inr': '20'}},
])
async def test_real_ai_revision_reaches_owner_transaction_selection(monkeypatch, db, draft):
    owner = onboard(db, telegram_id=uuid4().int % (2**62))
    r = db.propose(owner, [expense('25')], NOW)
    committed = db.confirm(owner, r['request_id'], r['revision'], NOW)
    transaction = committed['snapshot']['transactions'][-1]
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(mutation(draft))))
    try:
        flow = rendered_workflow(db, client)
        before = db.get_snapshot(owner)
        question = await flow.natural_language(owner, 'Undo the Metro expense', NOW)
        assert transaction['id'] in question['text']
        assert question['review'] is None and db.get_snapshot(owner) == before
        selected = await flow.answer(owner, transaction['id'], NOW)
        assert selected['review']['actions'][0]['transaction_id'] == transaction['id']
        assert 'reference' not in selected['review']['actions'][0]
        assert db.get_snapshot(owner) == before
        if draft['type'] == 'correct':
            assert selected['review']['actions'][0]['changes']['amount_inr'] == '20.00'
        review = selected['review']
        saved = await flow.decide(owner, review['request_id'], review['revision'], 'confirm', NOW)
        expected = 80000 if draft['type'] == 'undo' else 78000
        assert saved['result']['snapshot']['buckets']['Travel']['balance'] == expected
        assert await flow.decide(owner, review['request_id'], review['revision'], 'confirm', NOW) == saved
    finally:
        await client.close()


@pytest.mark.parametrize('answer,actions,pool_after', [
    ('From the pool', [{'type': 'allocate', 'amount_inr': '100', 'bucket_name': 'Travel'}], 10000),
    ('New income', [{'type': 'income', 'amount_inr': '100', 'description': 'New income'},
                    {'type': 'allocate', 'amount_inr': '100', 'bucket_name': 'Travel'}], 20000),
])
async def test_real_funding_clarification_answer_produces_review(monkeypatch, db, answer, actions, pool_after):
    owner = onboard(db, telegram_id=uuid4().int % (2**62))
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=response(mutation(*actions)))

    client = client_for(monkeypatch, handler)
    try:
        flow = rendered_workflow(db, client)
        before = db.get_snapshot(owner)
        question = await flow.natural_language(owner, 'Add 100 to Travel', NOW)
        assert question['review'] is None and 'pool' in question['text']
        assert not seen
        rejected = await flow.answer(owner, 'guess for me', NOW)
        assert rejected['review'] is None and not seen
        flow = rendered_workflow(db, client, flow.checkpointer)
        out = await flow.answer(owner, answer, NOW)
        assert out['review'] is not None
        assert db.get_snapshot(owner) == before
        assert len(seen) == 1
        context = json.loads(seen[0]['input'][0]['content'])
        assert context['funding_source'] == ('pool' if answer == 'From the pool' else 'income')
        review = out['review']
        assert [a['type'] for a in review['actions']] == [a['type'] for a in actions]
        saved = await flow.decide(owner, review['request_id'], review['revision'], 'confirm', NOW)
        assert saved['result']['snapshot']['pool'] == pool_after
        assert saved['result']['snapshot']['buckets']['Travel']['balance'] == 90000
    finally:
        await client.close()
