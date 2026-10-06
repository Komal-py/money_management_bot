"""R3 regressions: real PostgreSQL/graph, synthetic owners, no shared cleanup."""
import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import httpx

from budget_bot.ai.provider import AIInterpreter
from budget_bot.domain.errors import BudgetError
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.workflows import BudgetWorkflow

RECEIVED = datetime(2024, 2, 29, 20, tzinfo=timezone.utc)
NOW = RECEIVED + timedelta(days=2)


@pytest.fixture
def store():
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w7'
    assert os.environ.get('BUDGET_TEST_DATABASE_URL'), 'Approved test DB must be inherited.'
    db = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w7')
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def owner(store):
    identity = store.ensure_admin(uuid4().int % (2**62))
    review = store.propose(identity, [
        {'type': 'opening', 'amount_inr': '1000'},
        {'type': 'create_bucket', 'name': 'Travel'},
        {'type': 'allocate', 'amount_inr': '800', 'bucket_name': 'Travel'},
    ], RECEIVED)
    store.confirm(identity, review['request_id'], review['revision'], RECEIVED)
    return identity


def router(store, workflow):
    spec = importlib.util.find_spec('budget_bot.services.conversation')
    assert spec is not None, 'ConversationRouter module must exist.'
    module = importlib.import_module('budget_bot.services.conversation')
    return module.ConversationRouter(store, workflow, ReportService(store))


def expense(amount='25', bucket='Travel', description='Metro', day='today'):
    action = {'type': 'expense', 'amount_inr': amount, 'description': description, 'date_expression': day}
    if bucket is not None:
        action['bucket_name'] = bucket
    return action


def interpretation(kind='mutation', actions=None, query=None, **extra):
    return {'schema_version': 1, 'kind': kind, 'actions': actions or [], 'missing_fields': [],
            'clarification_question': None, 'query': query, **extra}


class ControlledInterpreter:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.calls = []

    async def interpret(self, text, bucket_names, received_at, timezone, **context):
        self.calls.append((text, bucket_names, received_at, timezone, context))
        return next(self.outputs)


async def test_new_nl_preserves_receipt_and_only_proposes(store, owner):
    interpreter = ControlledInterpreter(interpretation(actions=[expense()]))
    flow = BudgetWorkflow(store, interpreter)
    before = store.get_snapshot(owner)
    out = await router(store, flow).dispatch(owner, 'Spent 25 on Metro in Travel', RECEIVED, NOW)
    assert out['review']['actions'][0]['date_expression'] == '2024-03-01'
    assert interpreter.calls == [('Spent 25 on Metro in Travel', ['Travel'], RECEIVED, 'Asia/Kolkata', {})]
    assert store.get_snapshot(owner) == before
    state = await flow.graph.aget_state(flow.config(owner, out['review']['request_id']))
    assert state.next and state.tasks[0].interrupts


async def test_current_question_wins_and_resumes_real_graph(store, owner):
    interpreter = ControlledInterpreter()
    flow = BudgetWorkflow(store, interpreter)
    await flow.submit(owner, [expense(bucket=None)], RECEIVED)
    before = store.get_snapshot(owner)
    out = await router(store, flow).dispatch(owner, 'Travel', NOW, NOW)
    assert out['review']['actions'][0]['bucket_name'] == 'Travel'
    assert out['review']['actions'][0]['date_expression'] == '2024-03-01'
    assert not interpreter.calls
    assert store.get_snapshot(owner) == before


async def test_pending_review_never_bypassed_by_new_nl(store, owner):
    interpreter = ControlledInterpreter()
    flow = BudgetWorkflow(store, interpreter)
    review = (await flow.submit(owner, [expense()], RECEIVED))['review']
    before = store.get_snapshot(owner)
    with pytest.raises(BudgetError, match='Finish or cancel') as error:
        await router(store, flow).dispatch(owner, 'Show balances', RECEIVED, RECEIVED)
    assert error.value.code == 'pending_review'
    assert store.get_pending(owner)['request_id'] == review['request_id']
    assert not interpreter.calls and store.get_snapshot(owner) == before


def query(report='spending', period='today', **extra):
    return {'report': report, 'period': period, 'start': None, 'end': None, 'bucket_name': None, **extra}


@pytest.mark.parametrize('report', ['balances', 'spending', 'calendar'])
async def test_workflow_query_renders_real_reports(store, owner, report):
    review = store.propose(owner, [expense()], RECEIVED)
    store.confirm(owner, review['request_id'], review['revision'], RECEIVED)
    interpreter = ControlledInterpreter(interpretation(kind='query', query=query(report)))
    flow = BudgetWorkflow(store, interpreter)
    before = store.get_snapshot(owner)
    out = await router(store, flow).dispatch(owner, 'Show my report', RECEIVED, NOW)
    expected = {'balances': 'Total available: ₹975.00', 'spending': 'Total spent: ₹25.00',
                'calendar': 'March 2024'}[report]
    assert expected in out['text'] and 'validated' not in out['text']
    assert out['query'] == query(report)
    assert store.get_snapshot(owner) == before


@pytest.mark.parametrize('report', ['balances', 'spending', 'calendar'])
async def test_explicit_query_is_offline_and_receipt_anchored(store, owner, report):
    store.set_timezone(owner, 'America/Los_Angeles')
    flow = BudgetWorkflow(store)
    before = store.get_snapshot(owner)
    out = await router(store, flow).dispatch(owner, None, RECEIVED, NOW, query=query(report))
    expected = {'balances': 'Total available: ₹1000.00', 'spending': '2024-02-29 to 2024-02-29',
                'calendar': 'February 2024'}[report]
    assert expected in out['text']
    assert store.get_snapshot(owner) == before


@pytest.mark.parametrize('report', ['spending', 'calendar'])
@pytest.mark.parametrize('persisted_receipt', [True, False])
async def test_workflow_query_uses_persisted_receipt_or_current_receipt_fallback(
        store, owner, monkeypatch, report, persisted_receipt):
    original = datetime(2024, 2, 29, 18, 29, tzinfo=timezone.utc)
    reply = original + timedelta(minutes=2)  # Asia/Kolkata midnight and month boundary.
    review = store.propose(owner, [expense('3', day='2024-02-29'), expense('5', day='2024-03-01')], RECEIVED)
    store.confirm(owner, review['request_id'], review['revision'], RECEIVED)
    interpreter = ControlledInterpreter()
    flow = BudgetWorkflow(store, interpreter)
    output = {'query': query(report)}
    if persisted_receipt:
        output['query_received_at'] = original.isoformat()
    calls = []

    async def answer(owner_id, text, now):
        calls.append((owner_id, text, now))
        return output

    monkeypatch.setattr(flow, 'answer', answer)
    before, pending = store.get_snapshot(owner), store.get_pending(owner)
    out = await router(store, flow).dispatch(owner, 'today', reply, NOW)
    if report == 'spending':
        assert ('2024-02-29 to 2024-02-29' if persisted_receipt else '2024-03-01 to 2024-03-01') in out['text']
        assert ('Total spent: ₹3.00' if persisted_receipt else 'Total spent: ₹5.00') in out['text']
    else:
        assert out['text'] == ('February 2024' if persisted_receipt else 'March 2024')
    assert out['query'] == query(report) and 'query_received_at' not in out
    assert calls == [(owner, 'today', NOW)] and not interpreter.calls
    assert store.get_snapshot(owner) == before and store.get_pending(owner) == pending
    # Explicit query dispatch uses its supplied receipt, never workflow metadata.
    explicit = await router(store, flow).dispatch(owner, None, reply, NOW, query=query(report))
    assert ('Total spent: ₹5.00' if report == 'spending' else 'March 2024') in explicit['text']
    assert calls == [(owner, 'today', NOW)]
    assert store.get_snapshot(owner) == before and store.get_pending(owner) == pending


@pytest.mark.parametrize('bad_query', [
    {}, [], 'balances', query(report='sql'), query(report=[]), query(period=True),
    query(period='year'), query(owner_id='someone-else'), query(sql='SELECT private'),
    query(period='range'), query(period='range', start='20240229', end='2024-03-01'),
    query(period='range', start='2024-02-30', end='2024-03-01'),
    query(period='range', start='2024-03-02', end='2024-03-01'),
    query(start='2024-03-01'), query(bucket_name=True), query(bucket_name='Unknown'),
    query('balances', bucket_name='Unknown'), query('calendar', bucket_name='Unknown'),
    query('balances', bucket_name='Travel'), query('calendar', bucket_name='Travel'),
    query('calendar', period='range', start='2024-02-01', end='2024-03-01'),
])
async def test_invalid_queries_are_safe_read_only_errors(store, owner, bad_query):
    flow = BudgetWorkflow(store)
    before = store.get_snapshot(owner)
    out = await router(store, flow).dispatch(owner, None, RECEIVED, NOW, query=bad_query)
    assert '/help' in out['text']
    assert 'Total' not in out['text'] and 'SELECT private' not in out['text']
    assert out['keyboard'] == [] and not out.get('query')
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None


async def test_range_query_canonical_bucket_and_read_only_pending_review(store, owner):
    committed = store.propose(owner, [expense()], RECEIVED)
    store.confirm(owner, committed['request_id'], committed['revision'], RECEIVED)
    flow = BudgetWorkflow(store)
    await flow.submit(owner, [expense('10')], RECEIVED)
    pending = store.get_pending(owner)
    before = store.get_snapshot(owner)
    requested = query(period='range', start='2024-02-29', end='2024-03-01', bucket_name='travel')
    out = await router(store, flow).dispatch(owner, None, RECEIVED, NOW, query=requested)
    assert '2024-02-29 to 2024-03-01' in out['text'] and 'Total spent: ₹25.00' in out['text']
    assert out['query']['bucket_name'] == 'Travel' and requested['bucket_name'] == 'travel'
    assert store.get_pending(owner) == pending and store.get_snapshot(owner) == before


@pytest.mark.parametrize('report', ['spending', 'calendar'])
async def test_naive_receipt_is_safe_error(store, owner, report):
    out = await router(store, BudgetWorkflow(store)).dispatch(
        owner, None, RECEIVED.replace(tzinfo=None), NOW, query=query(report))
    assert '/help' in out['text'] and not out.get('query')


@pytest.mark.parametrize('payload', [
    'cal:2024-02', 'cal:2024-12', 'cal:0001-01', 'cal:9999-12',
])
async def test_calendar_month_navigation_is_real_and_offline(store, owner, payload):
    from budget_bot.telegram.calendar import month_view, parse_callback
    parsed = parse_callback(payload)
    before = store.get_snapshot(owner)
    route = router(store, BudgetWorkflow(store))
    out = await route.dispatch(owner, None, RECEIVED, NOW, callback_data=payload)
    assert out == month_view(parsed['year'], parsed['month'])
    for button in out['keyboard'][-1]:
        nav = parse_callback(button['data'])
        next_view = await route.dispatch(owner, None, RECEIVED, NOW, callback_data=button['data'])
        assert next_view == month_view(nav['year'], nav['month'])
    json.dumps(out)
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None


@pytest.mark.parametrize('payload', [
    'cal:2024-13', 'cal:0000-01', 'cal:2024-2', 'cal:2024-02:owner',
    'day:2023-02-29', 'day:2024-2-29', 'day:20240229', 'day:2024-02-29:-1',
    'day:2024-02-29:01', 'day:2024-02-29:1000000', 'day:2024-02-29:999999',
    'day:2024-02-29:1', 'day:2024-02-29:other-owner', 'cal:' + 'x' * 65,
])
async def test_invalid_calendar_buttons_are_safe(store, owner, payload):
    before = store.get_snapshot(owner)
    out = await router(store, BudgetWorkflow(store)).dispatch(
        owner, None, RECEIVED, NOW, callback_data=payload)
    assert '/calendar' in out['text'] and out['keyboard'] == []
    assert 'unavailable' not in out['text'] and payload not in out['text']
    assert store.get_snapshot(owner) == before


@pytest.mark.parametrize('payload', ['rev:other:1:confirm', 'setup:finish', 'menu:expense', '', 42])
async def test_unsupported_callbacks_never_become_nl_or_answers(store, owner, payload):
    flow = BudgetWorkflow(store, ControlledInterpreter())
    await flow.submit(owner, [expense(bucket=None)], RECEIVED)
    before = store.get_snapshot(owner)
    assert await router(store, flow).dispatch(owner, 'Travel', RECEIVED, NOW, callback_data=payload) is None
    assert store.get_pending(owner) is None and store.get_snapshot(owner) == before


async def test_calendar_day_pages_current_expenses_only_for_caller_owner(store, owner):
    other = store.ensure_admin(uuid4().int % (2**62))
    other_review = store.propose(other, [
        {'type': 'opening', 'amount_inr': '100'}, {'type': 'create_bucket', 'name': 'Private'},
        expense('77', 'Private', 'Other owner secret'),
    ], RECEIVED)
    store.confirm(other, other_review['request_id'], other_review['revision'], RECEIVED)
    for chunk in (range(8), range(8, 10)):
        review = store.propose(owner, [expense('1', description=f'Ride {n}') for n in chunk], RECEIVED)
        store.confirm(owner, review['request_id'], review['revision'], RECEIVED)
    rows = [t for t in store.get_snapshot(owner)['transactions'] if t['type'] == 'expense']
    correction = store.propose(owner, [
        {'type': 'correct', 'transaction_id': rows[0]['id'],
         'changes': {'amount_inr': '2', 'description': 'Corrected ride'}},
        {'type': 'undo', 'transaction_id': rows[1]['id']},
    ], RECEIVED)
    store.confirm(owner, correction['request_id'], correction['revision'], RECEIVED)
    flow = BudgetWorkflow(store, ControlledInterpreter())
    await flow.submit(owner, [expense(bucket=None)], RECEIVED)
    before, other_before = store.get_snapshot(owner), store.get_snapshot(other)
    route = router(store, flow)
    first = await route.dispatch(owner, None, RECEIVED, NOW, callback_data='day:2024-03-01')
    assert 'Total spent: ₹10.00' in first['text'] and '9 expenses' in first['text']
    assert 'Page 1 of 2' in first['text'] and first['text'].count(' | ') == 24
    next_button = next(b for row in first['keyboard'] for b in row if b['text'] == 'Next')
    second = await route.dispatch(owner, None, RECEIVED, NOW, callback_data=next_button['data'])
    assert 'Page 2 of 2' in second['text'] and second['text'].count(' | ') == 3
    combined = first['text'] + '\n' + second['text']
    assert 'Other owner secret' not in combined and 'Corrected ride' in combined
    assert rows[1]['description'] not in combined
    empty = await route.dispatch(owner, None, RECEIVED, NOW, callback_data='day:2024-02-29')
    assert 'No active expenses' in empty['text'] and 'Total spent: ₹0.00' in empty['text']
    assert store.get_snapshot(owner) == before and store.get_snapshot(other) == other_before
    # Navigation did not consume the outstanding question.
    answer = await route.dispatch(owner, 'Travel', RECEIVED, RECEIVED)
    assert answer['review']['actions'][0]['bucket_name'] == 'Travel'


def test_menu_offers_existing_commands_not_imaginary_callbacks():
    from budget_bot.services.conversation import ConversationRouter
    from budget_bot.telegram.commands import parse_command
    from budget_bot.telegram.menu import button_command, main_menu_keyboard
    out = ConversationRouter(None, None, None).menu()
    # Persistent reply keyboard only: buttons send text mapped to existing
    # commands, never callback data that ingress would have to trust.
    assert out['keyboard'] == main_menu_keyboard()
    assert all('callback_data' not in b and 'data' not in b for row in out['keyboard']['keyboard'] for b in row)
    for row in out['keyboard']['keyboard']:
        for button in row:
            command = button_command(button['text'])
            if command != '/spending':  # bare /spending opens the period picker in ingress
                parse_command(command, RECEIVED, 'Asia/Kolkata')
    assert 'confirm' in out['text'].lower() and 'virtual' in out['text'].lower()
    commands = [line.strip() for line in out['text'].splitlines() if line.startswith('/')]
    assert {'/balance', '/calendar', '/spending month', '/help', '/cancel'} <= set(commands)
    for command in commands:
        parse_command(command, RECEIVED, 'Asia/Kolkata')
    json.dumps(out)


@pytest.mark.parametrize('interpreter_enabled', [False, True])
async def test_store_pending_review_survives_missing_graph_and_ai_disabled(store, owner, interpreter_enabled):
    pending = store.propose(owner, [expense()], RECEIVED)
    before = store.get_snapshot(owner)
    interpreter = ControlledInterpreter(interpretation(kind='query', query=query('balances')))
    route = router(store, BudgetWorkflow(store, interpreter if interpreter_enabled else None))
    with pytest.raises(BudgetError) as error:
        await route.dispatch(owner, 'Show balances', RECEIVED, RECEIVED)
    assert error.value.code == 'pending_review'
    assert not interpreter.calls and store.get_snapshot(owner) == before
    assert store.get_pending(owner)['request_id'] == pending['request_id']
    out = await route.dispatch(owner, None, RECEIVED, RECEIVED, query=query('balances'))
    assert 'Total available: ₹1000.00' in out['text']


@pytest.mark.parametrize('text', [None, '', '   '])
async def test_empty_message_does_not_call_interpreter(store, owner, text):
    interpreter = ControlledInterpreter()
    route = router(store, BudgetWorkflow(store, interpreter))
    assert await route.dispatch(owner, text, RECEIVED, NOW) == route.menu()
    assert not interpreter.calls and store.get_pending(owner) is None


@pytest.mark.parametrize('failure', ['timeout', 'http503', 'invalid_output'])
async def test_real_sdk_outage_keeps_commands_reports_and_calendar_offline(store, owner, monkeypatch, failure):
    from budget_bot.telegram.commands import parse_command
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == 'example.invalid'
        if failure == 'timeout':
            raise httpx.ReadTimeout('Synthetic offline timeout', request=request)
        if failure == 'http503':
            return httpx.Response(503, json={'error': {'message': 'Synthetic service outage'}})
        return httpx.Response(200, json={'status': 'completed', 'output': []})

    real_client = httpx.AsyncClient

    class ControlledHTTP(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr('budget_bot.ai.provider.httpx.AsyncClient', ControlledHTTP)
    interpreter = AIInterpreter('https://example.invalid/v1', 'synthetic-test-key', 'test-model', timeout=0.2)
    try:
        flow = BudgetWorkflow(store, interpreter)
        route = router(store, flow)
        before = store.get_snapshot(owner)
        if failure == 'invalid_output':
            with pytest.raises(BudgetError) as error:
                await route.dispatch(owner, 'Show spending today', RECEIVED, NOW)
            assert error.value.code == 'invalid_model_output'
            assert 'Use commands' in error.value.message
        else:
            out = await route.dispatch(owner, 'Show spending today', RECEIVED, NOW)
            assert 'unavailable' in out['text'] and '/help' in out['text']
        assert len(calls) == 1 and store.get_pending(owner) is None
        assert store.get_snapshot(owner) == before
        for command in ('/balance', '/spending today', '/calendar'):
            parsed = parse_command(command, RECEIVED, 'Asia/Kolkata')
            out = await route.dispatch(owner, None, RECEIVED, NOW, query=parsed['query'])
            assert out['text'] and 'unavailable' not in out['text']
        day = await route.dispatch(owner, None, RECEIVED, NOW, callback_data='day:2024-03-01')
        assert 'No active expenses' in day['text'] and '/help' in route.menu()['text']
        parsed = parse_command('/expense 1 Travel Metro', RECEIVED, 'Asia/Kolkata')
        review = (await flow.submit(owner, parsed['actions'], RECEIVED))['review']
        assert store.get_snapshot(owner) == before
        committed = await flow.decide(owner, review['request_id'], review['revision'], 'confirm', RECEIVED)
        assert committed['result']['status'] == 'committed' and len(calls) == 1
    finally:
        await interpreter.close()


async def test_invalid_answer_stays_in_current_graph_then_valid_answer_preserves_receipt(store, owner):
    interpreter = ControlledInterpreter()
    flow = BudgetWorkflow(store, interpreter)
    await flow.submit(owner, [expense(bucket=None)], RECEIVED)
    before = store.get_snapshot(owner)
    route = router(store, flow)
    invalid = await route.dispatch(owner, 'Unknown bucket', NOW, NOW)
    assert 'Select an existing bucket' in invalid['text'] and not invalid['review']
    valid = await route.dispatch(owner, 'Travel', NOW, NOW)
    assert valid['review']['actions'][0]['date_expression'] == '2024-03-01'
    assert not interpreter.calls and store.get_snapshot(owner) == before


async def test_edit_answer_resumes_same_review_not_new_nl(store, owner):
    interpreter = ControlledInterpreter(interpretation(actions=[expense('12')]))
    flow = BudgetWorkflow(store, interpreter)
    old = (await flow.submit(owner, [expense()], RECEIVED))['review']
    await flow.decide(owner, old['request_id'], old['revision'], 'edit', RECEIVED)
    before = store.get_snapshot(owner)
    new = (await router(store, flow).dispatch(owner, 'Actually 12 for Metro', RECEIVED, RECEIVED))['review']
    assert new['request_id'] == old['request_id'] and new['revision'] > old['revision']
    assert new['actions'][0]['amount_inr'] == '12.00'
    assert len(interpreter.calls) == 1 and 'Owner answer: Actually 12 for Metro' in interpreter.calls[0][0]
    assert store.get_snapshot(owner) == before


async def test_unknown_nl_query_bucket_is_safe_and_never_reads_other_owner(store, owner):
    interpreter = ControlledInterpreter(interpretation(kind='query', query=query(bucket_name='Unknown')))
    before = store.get_snapshot(owner)
    with pytest.raises(BudgetError) as error:
        await router(store, BudgetWorkflow(store, interpreter)).dispatch(owner, 'Unknown spending', RECEIVED, NOW)
    assert error.value.code == 'unknown_bucket' and error.value.message == 'Select an existing bucket.'
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None


@pytest.mark.parametrize('mode', ['report', 'day', 'nl'])
async def test_unexpected_database_failures_remain_retryable(store, owner, monkeypatch, mode):
    def unavailable(*args, **kwargs):
        raise RuntimeError('Synthetic database outage')

    route = router(store, BudgetWorkflow(store))
    method = {'report': 'get_snapshot', 'day': 'spending', 'nl': 'get_pending'}[mode]
    monkeypatch.setattr(store, method, unavailable)
    kwargs = {'report': {'query': query()}, 'day': {'callback_data': 'day:2024-03-01'}, 'nl': {}}[mode]
    with pytest.raises(RuntimeError, match='Synthetic database outage'):
        await route.dispatch(owner, 'Show balances', RECEIVED, NOW, **kwargs)


@pytest.mark.parametrize(('period', 'dates'), [
    ('week', '2024-02-26 to 2024-03-03'), ('month', '2024-03-01 to 2024-03-31'),
])
async def test_real_report_periods_ignore_processing_clock(store, owner, period, dates):
    review = store.propose(owner, [expense()], RECEIVED)
    store.confirm(owner, review['request_id'], review['revision'], RECEIVED)
    route = router(store, BudgetWorkflow(store))
    out = await route.dispatch(owner, None, RECEIVED, NOW + timedelta(days=40), query=query(period=period))
    assert dates in out['text'] and 'Total spent: ₹25.00' in out['text']


async def test_real_sdk_validated_query_reaches_deterministic_calendar(store, owner, monkeypatch):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body['input'][0]['content'])
        assert context == {'message': 'Show my calendar', 'bucket_names': ['Travel'],
                           'local_date': '2024-03-01', 'timezone': 'Asia/Kolkata'}
        assert body['store'] is False
        calls.append(request)
        return httpx.Response(200, json={
            'id': 'resp_synthetic', 'object': 'response', 'created_at': 1, 'model': 'test-model',
            'status': 'completed', 'error': None, 'incomplete_details': None,
            'output': [{'type': 'message', 'id': 'msg_synthetic', 'status': 'completed',
                        'role': 'assistant', 'content': [{'type': 'output_text', 'annotations': [],
                        'text': json.dumps(interpretation(kind='query', query=query('calendar')))}]}],
        })

    real_client = httpx.AsyncClient

    class ControlledHTTP(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr('budget_bot.ai.provider.httpx.AsyncClient', ControlledHTTP)
    interpreter = AIInterpreter('https://example.invalid/v1', 'synthetic-test-key', 'test-model')
    try:
        before = store.get_snapshot(owner)
        out = await router(store, BudgetWorkflow(store, interpreter)).dispatch(
            owner, 'Show my calendar', RECEIVED, NOW)
        assert out['text'] == 'March 2024' and out['query'] == query('calendar')
        assert len(calls) == 1 and store.get_snapshot(owner) == before and store.get_pending(owner) is None
    finally:
        await interpreter.close()
