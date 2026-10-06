"""Report clarification must terminate the durable graph without financial writes."""
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from budget_bot.domain.errors import BudgetError
from budget_bot.services.conversation import ConversationRouter
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.workflows import BudgetWorkflow, postgres_checkpointer
from test_ai_provider import client_for, response
from test_workflow import expense, onboard

RECEIVED = datetime(2026, 10, 6, 18, 29, tzinfo=timezone.utc)
ANSWERED = RECEIVED + timedelta(minutes=2)


@pytest.fixture
def store():
    assert os.environ['BUDGET_TEST_SCHEMA'] == 'test_w6'
    db = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w6')
    db.initialize()
    yield db
    db.close()


def report_query(**overrides):
    value = {'report': 'spending', 'period': 'today', 'start': None, 'end': None, 'bucket_name': 'Travel'}
    return {'schema_version': 1, 'kind': 'query', 'actions': [], 'missing_fields': [],
            'clarification_question': None, 'query': value | overrides}


def clarify():
    return {'schema_version': 1, 'kind': 'clarification', 'actions': [], 'missing_fields': ['period'],
            'clarification_question': 'Which reporting period?', 'query': None}


async def test_report_clarification_query_survives_restart_and_preserves_original_receipt(store, monkeypatch):
    owner = onboard(store, uuid4().int % (2**62))
    baseline = store.get_snapshot(owner)
    outputs = iter([clarify(), clarify(), report_query()])
    wires = []
    def handler(request):
        wires.append(json.loads(request.content))
        return httpx.Response(200, json=response(next(outputs)))
    client = client_for(monkeypatch, handler)
    try:
        url = os.environ['BUDGET_TEST_DATABASE_URL']
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            flow = BudgetWorkflow(store, client, saver)
            question = await flow.natural_language(owner, 'Travel spending', RECEIVED)
            assert question['review'] is None and 'reporting period' in question['text']
            assert store.get_snapshot(owner) == baseline
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            flow = BudgetWorkflow(store, client, saver)
            still_question = await flow.answer(owner, 'a reporting period please', ANSWERED)
            assert still_question['review'] is None and 'reporting period' in still_question['text']
            resolved = await flow.answer(owner, 'today', ANSWERED)
            assert resolved['query'] == report_query()['query']
            assert resolved['query_received_at'] == RECEIVED.isoformat()
            assert resolved['review'] is None and resolved['result'] is None
            assert store.get_pending(owner) is None and store.get_snapshot(owner) == baseline
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            flow = BudgetWorkflow(store, client, saver)
            with pytest.raises(BudgetError) as no_question:
                await flow.answer(owner, 'today', ANSWERED)
            assert no_question.value.code == 'no_question'
        assert len(wires) == 3
        for wire in wires:
            payload = json.loads(wire['input'][0]['content'])
            assert payload['local_date'] == '2026-10-06'
    finally:
        await client.close()


async def test_report_origin_period_then_bucket_clarification_can_finish_query_after_restart(store, monkeypatch):
    owner = onboard(store, uuid4().int % (2**62))
    baseline = store.get_snapshot(owner)
    bucket_question = clarify() | {'missing_fields': ['bucket_name'],
                                   'clarification_question': 'Which reporting bucket?'}
    outputs = iter([clarify(), bucket_question, report_query()])
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(next(outputs))))
    try:
        url = os.environ['BUDGET_TEST_DATABASE_URL']
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            flow = BudgetWorkflow(store, client, saver)
            await flow.natural_language(owner, 'Show spending', RECEIVED)
            question = await flow.answer(owner, 'today', ANSWERED)
            assert 'query' not in question and 'bucket' in question['text']
            state = await flow.graph.aget_state(flow.config(owner, await flow._active(owner)))
            assert state.next and state.values['missing_fields'] == ['bucket_name']
            assert question['review'] is None and question['result'] is None
            assert store.get_pending(owner) is None and store.get_snapshot(owner) == baseline
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            flow = BudgetWorkflow(store, client, saver)
            resolved = await flow.answer(owner, 'Travel', ANSWERED)
            assert resolved['query'] == report_query()['query']
            assert resolved['query_received_at'] == RECEIVED.isoformat()
            assert resolved['review'] is None and resolved['result'] is None
            assert store.get_pending(owner) is None and store.get_snapshot(owner) == baseline
            with pytest.raises(BudgetError) as no_question:
                await flow.answer(owner, 'Travel', ANSWERED)
            assert no_question.value.code == 'no_question'
    finally:
        await client.close()


@pytest.mark.parametrize('mode', ['funding', 'expense_clarification', 'edit', 'changed_clarification',
                                  'bucket_only', 'report_to_mutation'])
async def test_query_cannot_bypass_mutation_clarification_or_pending_edit(store, monkeypatch, mode):
    owner = onboard(store, uuid4().int % (2**62))
    baseline = store.get_snapshot(owner)
    missing = {'funding': 'funding_source', 'bucket_only': 'bucket_name'}.get(mode, 'description')
    first = clarify() | {'missing_fields': [missing]}
    items = [report_query()] if mode == 'edit' else [first, report_query()]
    if mode == 'changed_clarification':
        items = [first, clarify(), report_query()]
    elif mode == 'report_to_mutation':
        items = [clarify(), first, clarify(), report_query()]
    outputs = iter(items)
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(next(outputs))))
    try:
        flow = BudgetWorkflow(store, client)
        if mode == 'edit':
            review = (await flow.submit(owner, [expense()], RECEIVED))['review']
            await flow.decide(owner, review['request_id'], review['revision'], 'edit', ANSWERED)
            pending = store.get_pending(owner)
        else:
            await flow.natural_language(owner,
                'Record Metro expense 25' if mode == 'bucket_only' else 'Prepare a budgeting action', RECEIVED)
            pending = None
        answer = 'from the pool' if mode == 'funding' else 'today'
        if mode == 'changed_clarification':
            await flow.answer(owner, 'reporting period instead', ANSWERED)
        elif mode == 'report_to_mutation':
            await flow.answer(owner, 'describe a mutation', ANSWERED)
            await flow.answer(owner, 'reporting period again', ANSWERED)
        output = await flow.answer(owner, answer, ANSWERED)
        assert 'query' not in output
        assert store.get_snapshot(owner) == baseline
        assert store.get_pending(owner) == pending
        state = await flow.graph.aget_state(flow.config(owner, await flow._active(owner)))
        assert state.next and state.values['question']
    finally:
        await client.close()


@pytest.mark.parametrize('bad_query', [
    {'period': 'range', 'start': '2026-10-08', 'end': '2026-10-07'},
    {'bucket_name': 'NotAnOwnerBucket'},
])
async def test_invalid_query_after_report_clarification_remains_a_question(store, monkeypatch, bad_query):
    owner = onboard(store, uuid4().int % (2**62))
    baseline = store.get_snapshot(owner)
    outputs = iter([clarify(), report_query(**bad_query)])
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(next(outputs))))
    try:
        flow = BudgetWorkflow(store, client)
        await flow.natural_language(owner, 'Travel spending', RECEIVED)
        output = await flow.answer(owner, 'today', ANSWERED)
        assert 'query' not in output and output['review'] is None
        assert store.get_snapshot(owner) == baseline and store.get_pending(owner) is None
        state = await flow.graph.aget_state(flow.config(owner, await flow._active(owner)))
        assert state.next and state.values['question']
    finally:
        await client.close()


@pytest.mark.parametrize('report', ['spending', 'calendar'])
async def test_actual_router_graph_query_restart_uses_original_day_or_month(store, monkeypatch, report):
    owner = onboard(store, uuid4().int % (2**62))
    receipt = datetime(2026, 9, 30, 18, 29, tzinfo=timezone.utc)
    reply = receipt + timedelta(minutes=2)
    for stamp, amount in ((receipt, '3'), (reply, '5')):
        review = store.propose(owner, [expense(amount)], stamp)
        store.confirm(owner, review['request_id'], review['revision'], stamp)
    before = store.get_snapshot(owner)
    query = report_query(report=report, bucket_name=None if report == 'calendar' else 'Travel')
    outputs = iter([clarify(), query])
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(next(outputs))))
    try:
        url = os.environ['BUDGET_TEST_DATABASE_URL']
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            flow = BudgetWorkflow(store, client, saver)
            route = ConversationRouter(store, flow, ReportService(store))
            initial = await route.dispatch(owner, 'Show spending' if report == 'spending' else 'Show calendar',
                                           receipt, receipt)
            assert 'reporting period' in initial['text'] and initial['review'] is None
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            flow = BudgetWorkflow(store, client, saver)
            route = ConversationRouter(store, flow, ReportService(store))
            resolved = await route.dispatch(owner, 'today', reply, reply)
            if report == 'spending':
                assert '2026-09-30 to 2026-09-30' in resolved['text']
                assert 'Total spent: ₹3.00' in resolved['text']
            else:
                assert resolved['text'] == 'September 2026'
            assert resolved['query'] == query['query'] and 'query_received_at' not in resolved
            assert store.get_snapshot(owner) == before and store.get_pending(owner) is None
    finally:
        await client.close()
