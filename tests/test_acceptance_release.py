"""Independent application gates and strict defect repros, not live/release sign-off.

Real test_w8 PostgreSQL, controller/services/router/graph and controlled SDK wire.
Original incomplete quota-attempt suite is preserved in commit 9fe6a86.
"""
import importlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from telegram import Bot
from telegram.error import TimedOut
from telegram.request import BaseRequest

from budget_bot.ai import render_result, render_review
from budget_bot.ai.provider import AIInterpreter
from budget_bot.controller import BudgetController
from budget_bot.domain.errors import BudgetError
from budget_bot.services.access import AccessService
from budget_bot.services.onboarding import OnboardingService
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.storage.models import Account, Batch, Outbox, Request, TransactionRevision
from budget_bot.telegram.calendar import day_view
from budget_bot.telegram.transport import TelegramTransport
from budget_bot.workflows import BudgetWorkflow, postgres_checkpointer

NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
pytestmark = pytest.mark.postgres


def synthetic_id():
    return uuid4().int % (2**52) + 1


@pytest.fixture
def store():
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w8', 'Use only approved schema test_w8'
    url = os.environ.get('BUDGET_TEST_DATABASE_URL')
    assert url, 'Approved test database must be inherited; no .env fallback in this suite'
    db = None
    try:
        db = BudgetStore(url, schema='test_w8')
        db.initialize()
    except Exception:
        if db is not None:
            db.close()
        pytest.fail('Approved test database initialization failed; details withheld', pytrace=False)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def actors(store):
    admin_id, member_id = synthetic_id(), synthetic_id()
    admin = store.ensure_admin(admin_id)
    member = store.redeem_invite(store.create_invite(admin, NOW), member_id, NOW)
    return admin_id, admin, member_id, member


@pytest.fixture
def onboarding(store):
    service = OnboardingService(store)
    service.initialize()
    return service


def workflow(store, interpreter=None, saver=None):
    flow = BudgetWorkflow(store, interpreter, saver)
    # Explicit documented renderer-binding seam, not an alternative workflow.
    flow.render_review = render_review
    flow.render_result = render_result
    return flow


def controller(store, onboarding, flow=None):
    return BudgetController(store, flow or workflow(store), onboarding, ReportService(store), AccessService(store))


def message(actor, text, *, at=NOW, chat_type='private', sender=None, is_bot=False):
    return {'update_id': synthetic_id(), 'message': {
        'message_id': 1, 'date': int(at.timestamp()),
        'chat': {'id': actor, 'type': chat_type},
        'from': {'id': actor if sender is None else sender, 'is_bot': is_bot, 'first_name': 'Synthetic'},
        'text': text}}


def callback(actor, data):
    payload = message(actor, '')
    msg = payload.pop('message')
    payload['callback_query'] = {'id': str(uuid4()), 'chat_instance': 'synthetic-r4-chat',
                                 'from': msg['from'], 'message': msg, 'data': data}
    return payload


def buttons(output):
    return [button['data'] for row in output['keyboard'] for button in row]


def review_button(reply, decision):
    selected = [data for data in buttons(reply) if data.startswith('rev:') and data.endswith(':' + decision)]
    assert len(selected) == 1, 'Review must expose one owner-bound ' + decision + ' button'
    return selected[0]


def setup_button(reply, action):
    selected = [data for data in buttons(reply) if data.startswith('setup:' + action + ':')]
    assert len(selected) == 1
    assert str(UUID(selected[0].split(':')[2])) == selected[0].split(':')[2]
    return selected[0]


def assert_same_review(actual, expected):
    assert datetime.fromisoformat(actual['expires_at']) == datetime.fromisoformat(expected['expires_at'])
    assert {k: v for k, v in actual.items() if k != 'expires_at'} == {
        k: v for k, v in expected.items() if k != 'expires_at'}


def commit(store, owner, actions):
    review = store.propose(owner, actions, NOW)
    result = store.confirm(owner, review['request_id'], review['revision'], NOW)
    assert result['status'] == 'committed'
    return result


def seed(store, owner):
    return commit(store, owner, [
        {'type': 'opening', 'amount_inr': '100'},
        {'type': 'create_bucket', 'name': 'Travel'},
        {'type': 'allocate', 'amount_inr': '50', 'bucket_name': 'Travel'},
    ])


def expense(amount='10', description='Synthetic Metro'):
    return {'type': 'expense', 'amount_inr': amount, 'bucket_name': 'Travel', 'description': description}


def conversation_class():
    # Runtime failure is an explicit release assertion, never a collection error.
    name = 'budget_bot.services.conversation'
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        pytest.fail('Missing release boundary: budget_bot.services.conversation.ConversationRouter', pytrace=False)
    router = getattr(module, 'ConversationRouter', None)
    assert router is not None, 'Missing frozen ConversationRouter API'
    return router


async def test_ingress_invite_starts_guided_setup(store, actors, onboarding):
    _, admin, _, _ = actors
    actor = synthetic_id()
    code = store.create_invite(admin, NOW)
    app = controller(store, onboarding)
    replies = await app.handle(message(actor, '/start ' + code), NOW)
    user = store.get_user(actor)
    assert user is not None, 'Private /start invite must redeem before rejecting unknown users'
    assert user['active'] and not user['admin']
    assert 'opening' in replies[0]['text'].lower()
    assert onboarding.active(user['owner_id'])
    assert store.get_snapshot(user['owner_id'])['pool'] == 0


@pytest.mark.parametrize('decision', ['confirm', 'edit', 'cancel'])
async def test_ingress_setup_review_lifecycle(store, actors, onboarding, decision):
    _, _, actor, owner = actors
    app = controller(store, onboarding)
    initial = store.get_snapshot(owner)
    reply = (await app.handle(message(actor, '/start'), NOW))[0]
    assert 'opening' in reply['text'].lower(), 'Registered unfinished owner must enter setup'
    for value in ('100', 'Travel', '50'):
        reply = (await app.handle(message(actor, value), NOW))[0]
    reply = (await app.handle(callback(actor, setup_button(reply, 'finish')), NOW))[0]
    original = store.get_pending(owner)
    assert original is not None and store.get_snapshot(owner) == initial
    data = review_button(reply, decision)
    reply = (await app.handle(callback(actor, data), NOW))[0]
    if decision == 'confirm':
        assert store.get_snapshot(owner)['pool'] == 5000
        assert store.get_snapshot(owner)['onboarded']
        assert 'Recorded.' in reply['text']
    else:
        assert store.get_snapshot(owner) == initial
        assert store.get_pending(owner) is None
        if decision == 'edit':
            assert 'opening' in reply['text'].lower()
            for value in ('80', 'Travel', '30'):
                reply = (await app.handle(message(actor, value), NOW))[0]
            new_reply = (await app.handle(callback(actor, setup_button(reply, 'finish')), NOW))[0]
            newer = store.get_pending(owner)
            assert newer['request_id'] != original['request_id']
            await app.handle(callback(actor, data), NOW)
            assert store.get_pending(owner) == newer, 'Stale Edit must not invalidate newer setup review'
            await app.handle(callback(actor, review_button(new_reply, 'confirm')), NOW)
            assert store.get_snapshot(owner)['pool'] == 5000
        else:
            assert 'cancel' in reply['text'].lower()


async def test_ingress_admin_metadata_and_member_denial(store, actors, onboarding):
    admin_id, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    reply = (await app.handle(message(admin_id, '/users'), NOW))[0]
    assert 'Registered access:' in reply['text'], 'Controller must dispatch access-only administrator API'
    assert str(actor) in reply['text']
    assert 'Travel' not in reply['text'] and '₹' not in reply['text']
    denied = (await app.handle(message(actor, '/invite'), NOW))[0]
    assert 'administrator' in denied['text'].lower()
    await app.handle(message(admin_id, '/revoke ' + str(actor)), NOW)
    assert not store.get_user(actor)['active']


async def test_ingress_timezone_and_command_cancel(store, actors, onboarding):
    _, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    await app.handle(message(actor, '/timezone UTC'), NOW)
    assert store.get_snapshot(owner)['timezone'] == 'UTC', 'Timezone command currently falls through'
    before = store.get_snapshot(owner)
    reply = (await app.handle(message(actor, '/expense 10 Travel "Synthetic Metro"'), NOW))[0]
    review_button(reply, 'confirm')
    await app.handle(message(actor, '/cancel'), NOW)
    assert store.get_pending(owner) is None
    assert store.get_snapshot(owner) == before


async def test_ingress_calendar_uses_owner_local_receipt_and_day_scope(store, actors, onboarding):
    _, admin, actor, owner = actors
    seed(store, owner)
    seed(store, admin)
    commit(store, owner, [expense()])
    commit(store, admin, [expense('99', 'FOREIGN_SYNTHETIC_ONLY')])
    before = store.get_snapshot(owner)
    app = controller(store, onboarding)
    boundary = datetime(2026, 9, 30, 20, tzinfo=timezone.utc)
    reply = (await app.handle(message(actor, '/calendar', at=boundary), boundary))[0]
    assert reply['text'] == 'October 2026', 'Calendar must use Asia/Kolkata receipt, not UTC month'
    assert 'cal:2026-11' in buttons(reply)
    day = (await app.handle(callback(actor, 'day:2026-10-06'), NOW))[0]
    assert 'Total spent: ₹10.00' in day['text']
    assert 'FOREIGN_SYNTHETIC_ONLY' not in day['text']
    empty = (await app.handle(callback(actor, 'day:2026-10-04'), NOW))[0]
    assert 'No active expenses' in empty['text']
    assert store.get_snapshot(owner) == before


async def test_ingress_foreign_review_is_safe_reply_not_retry_poison(store, actors, onboarding):
    _, admin, actor, owner = actors
    seed(store, admin)
    seed(store, owner)
    review = store.propose(admin, [expense()], NOW)
    before = store.get_snapshot(owner)
    app = controller(store, onboarding)
    try:
        reply = (await app.handle(callback(actor, f'rev:{review["request_id"]}:1:confirm'), NOW))[0]
    except BudgetError as error:
        pytest.fail('Foreign review BudgetError must become safe reply; escaped code=' + error.code, pytrace=False)
    assert 'not found' in reply['text'].lower() or 'not available' in reply['text'].lower()
    assert review['request_id'] not in reply['text']
    assert store.get_snapshot(owner) == before
    assert_same_review(store.get_pending(admin), review)


async def test_ingress_invalid_finance_is_safe_and_no_write(store, actors, onboarding):
    _, _, actor, owner = actors
    seed(store, owner)
    before = store.get_snapshot(owner)
    try:
        reply = (await controller(store, onboarding).handle(message(actor, '/allocate 51 Travel'), NOW))[0]
    except BudgetError as error:
        pytest.fail('Planner validation must become safe reply; escaped code=' + error.code, pytrace=False)
    assert reply['text'] == 'pool has ₹50.00; restore ₹1.00 to cover ₹51.00'
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None


async def test_ingress_optional_conversation_injection_contract(store, onboarding):
    flow, reports = workflow(store), ReportService(store)
    conversation = conversation_class()(store, flow, reports)
    app = BudgetController(store, flow, onboarding, reports, AccessService(store), conversation=conversation)
    assert app._conversation() is conversation


async def test_ingress_conversation_module_runtime_gate():
    router = conversation_class()
    assert callable(router.dispatch) and callable(router.menu)


def interpretation(kind='mutation', *, actions=(), query=None):
    return {'schema_version': 1, 'kind': kind, 'actions': list(actions), 'missing_fields': [],
            'clarification_question': None, 'query': query}


def sdk_client(monkeypatch, handler):
    original_client = httpx.AsyncClient

    class ControlledHTTP(original_client):
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr('budget_bot.ai.provider.httpx.AsyncClient', ControlledHTTP)
    return AIInterpreter('https://example.invalid/v1', 'synthetic-r4-key', 'synthetic-model', timeout=0.2)


def sdk_response(value):
    return httpx.Response(200, json={
        'id': 'resp_r4', 'object': 'response', 'created_at': 1, 'status': 'completed',
        'model': 'synthetic-model', 'output': [{
            'type': 'message', 'id': 'msg_r4', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': json.dumps(value), 'annotations': []}]}]})


async def test_ingress_nlp_clarification_answer_and_deterministic_query(store, actors, onboarding, monkeypatch):
    _, _, actor, owner = actors
    seed(store, owner)
    replies = iter([
        interpretation(actions=[{'type': 'allocate', 'amount_inr': '5', 'bucket_name': 'Travel'}]),
        interpretation('query', query={'report': 'balances', 'period': 'month',
                                      'start': None, 'end': None, 'bucket_name': None}),
    ])
    client = sdk_client(monkeypatch, lambda request: sdk_response(next(replies)))
    try:
        app = controller(store, onboarding, workflow(store, client))
        before = store.get_snapshot(owner)
        question = (await app.handle(message(actor, 'Add 5 to Travel'), NOW))[0]
        assert 'pool' in question['text'].lower(), 'NL must reach graph clarification, not generic help'
        review = (await app.handle(message(actor, 'From the pool'), NOW))[0]
        assert store.get_snapshot(owner) == before
        await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
        report = (await app.handle(message(actor, 'Show my balances'), NOW))[0]
        assert report['text'] == ReportService(store).balances(owner)
        assert 'Report request validated.' not in report['text']
    finally:
        await client.close()


@pytest.mark.parametrize('bad', ['cal:2026-13', 'day:2026-02-30', 'day:2026-10-06:999999'])
async def test_ingress_calendar_invalid_arguments_are_safe(store, actors, onboarding, bad):
    _, _, actor, owner = actors
    seed(store, owner)
    before = store.get_snapshot(owner)
    reply = (await controller(store, onboarding).handle(callback(actor, bad), NOW))[0]
    assert 'invalid' in reply['text'].lower() or 'out of range' in reply['text'].lower(), (
        'Calendar validation must run instead of treating every callback as unsupported')
    assert store.get_snapshot(owner) == before


@pytest.mark.parametrize('decision', ['confirm', 'edit', 'cancel'])
async def test_service_setup_graph_handoff_and_reopen(store, actors, onboarding, decision):
    _, _, _, owner = actors
    before = store.get_snapshot(owner)
    for value in ('/start', '100', 'Travel', '50', 'setup:finish'):
        output = onboarding.handle(owner, value, NOW)
    review = output['review']
    assert review and store.get_snapshot(owner) == before
    # Checkpoint tables are library-owned but stay inside the sole approved schema.
    url = os.environ['BUDGET_TEST_DATABASE_URL']
    async with postgres_checkpointer(url, schema='test_w8') as saver:
        flow = workflow(store, saver=saver)
        handoff = await flow.submit(owner, review['actions'], NOW, request_id=review['request_id'])
        assert handoff['review']['plan'] == review['plan']
        assert 'INR 50.00' in handoff['text']
        assert len(buttons(handoff)) == 3
    async with postgres_checkpointer(url, schema='test_w8') as saver:
        flow = workflow(store, saver=saver)
        if decision == 'edit':
            replacement = [{**a, 'amount_inr': '80'} if a['type'] == 'opening' else a
                           for a in review['actions']]
            edited = await flow.decide(owner, review['request_id'], review['revision'], 'edit', NOW,
                                       edited_actions=replacement)
            newer = edited['review']
            assert newer['revision'] > review['revision']
            assert store.get_snapshot(owner) == before
            stale = await flow.decide(owner, review['request_id'], review['revision'], 'cancel', NOW)
            assert stale['review']['revision'] == newer['revision']
            assert store.get_pending(owner)['revision'] == newer['revision']
            review = newer
        output = await flow.decide(owner, review['request_id'], review['revision'],
                                   'cancel' if decision == 'cancel' else 'confirm', NOW)
        assert await flow.decide(owner, review['request_id'], review['revision'], 'confirm', NOW) == output
    if decision == 'cancel':
        assert output['result']['status'] == 'cancelled'
        assert store.get_snapshot(owner) == before
    else:
        expected_pool = 3000 if decision == 'edit' else 5000
        assert output['result']['snapshot']['pool'] == expected_pool
        assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 5000
        assert onboarding.handle(owner, '/start', NOW)['done']


def test_service_invites_access_metadata_revocation_and_single_use(store, actors):
    _, admin, actor, owner = actors
    seed(store, owner)
    access = AccessService(store)
    code = store.create_invite(admin, NOW)
    new_actor = synthetic_id()
    new_owner = store.redeem_invite(code, new_actor, NOW)
    with pytest.raises(BudgetError) as error:
        store.redeem_invite(code, synthetic_id(), NOW)
    assert error.value.code == 'invalid_invite'
    expired = store.create_invite(admin, NOW)
    with pytest.raises(BudgetError) as error:
        store.redeem_invite(expired, synthetic_id(), NOW + timedelta(hours=25))
    assert error.value.code == 'invalid_invite'
    for row in store.list_access(admin):
        assert set(row) <= {'owner_id', 'id', 'telegram_id', 'active', 'admin', 'timezone', 'onboarded'}
    with pytest.raises(BudgetError) as error:
        access.handle_admin(owner, 'users', [], NOW)
    assert error.value.code == 'access_denied'
    response = access.handle_admin(admin, 'users', [], NOW)
    assert str(actor) in response['text'] and 'Travel' not in response['text'] and '₹' not in response['text']
    access.handle_admin(admin, 'revoke', [str(new_actor)], NOW)
    with pytest.raises(BudgetError) as error:
        store.get_snapshot(new_owner)
    assert error.value.code == 'access_denied'
    fresh_invite = store.create_invite(admin, NOW)
    with pytest.raises(BudgetError) as error:
        store.redeem_invite(fresh_invite, new_actor, NOW)
    assert error.value.code == 'already_registered'
    assert not store.get_user(new_actor)['active']


@pytest.mark.parametrize('actor_mode', ['group', 'foreign_sender', 'bot', 'unknown', 'revoked'])
async def test_supported_private_identity_gates(store, actors, onboarding, actor_mode):
    _, admin, actor, owner = actors
    seed(store, owner)
    before = store.get_snapshot(owner)
    payload = message(actor, '/balance')
    if actor_mode == 'group':
        payload['message']['chat']['type'] = 'supergroup'
    elif actor_mode == 'foreign_sender':
        payload['message']['from']['id'] = synthetic_id()
    elif actor_mode == 'bot':
        payload['message']['from']['is_bot'] = True
    elif actor_mode == 'unknown':
        payload = message(synthetic_id(), '/balance')
    else:
        store.revoke_user(admin, actor, NOW)
    reply = await controller(store, onboarding).handle(payload, NOW)
    if actor_mode in {'unknown', 'revoked'}:
        assert ('revoked' if actor_mode == 'revoked' else 'invite-only') in reply[0]['text']
        assert 'Travel' not in reply[0]['text'] and '₹' not in reply[0]['text']
    else:
        assert reply == []
    if actor_mode != 'revoked':
        assert store.get_snapshot(owner) == before
    else:
        with pytest.raises(BudgetError) as error:
            store.get_snapshot(owner)
        assert error.value.code == 'access_denied'


async def test_supported_offline_commands_review_replay_and_reports(store, actors, onboarding):
    _, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    before = store.get_snapshot(owner)
    reply = (await app.handle(message(actor, '/expense 10 Travel "Synthetic Metro"'), NOW))[0]
    assert store.get_snapshot(owner) == before
    data = review_button(reply, 'confirm')
    saved = await app.handle(callback(actor, data), NOW)
    assert await app.handle(callback(actor, data), NOW) == saved
    assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 4000
    assert store.spending(owner, NOW.date(), NOW.date())['count'] == 1
    report = (await app.handle(message(actor, '/spending today'), NOW))[0]
    assert report['text'] == ReportService(store).spending(owner, 'today', NOW)
    unavailable = await app.workflow.natural_language(owner, 'Record another expense', NOW)
    assert 'unavailable' in unavailable['text'] and unavailable['review'] is None
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Batch).where(Batch.owner_id == owner)) == 2


async def test_service_funding_clarification_persists_and_does_not_cross_owners(store, actors, monkeypatch):
    _, admin, _, owner = actors
    seed(store, owner)
    seed(store, admin)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return sdk_response(interpretation(actions=[
            {'type': 'allocate', 'amount_inr': '5', 'bucket_name': 'Travel'}]))

    client = sdk_client(monkeypatch, handler)
    before = store.get_snapshot(owner)
    try:
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            flow = workflow(store, client, saver)
            question = await flow.natural_language(owner, 'Add 5 to Travel', NOW)
            assert 'pool' in question['text'] and question['review'] is None and not seen
            invalid = await flow.answer(owner, 'guess for me', NOW)
            assert invalid['review'] is None and not seen
            with pytest.raises(BudgetError) as error:
                await flow.answer(admin, 'From the pool', NOW)
            assert error.value.code == 'no_question'
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            flow = workflow(store, client, saver)
            review = (await flow.answer(owner, 'From the pool', NOW))['review']
            assert review and store.get_snapshot(owner) == before and len(seen) == 1
            body = seen[0]
            assert body['store'] is False
            context = json.loads(body['input'][0]['content'])
            assert context['funding_source'] == 'pool'
            assert set(context) == {'message', 'bucket_names', 'local_date', 'timezone', 'funding_source'}
            assert owner not in json.dumps(context) and admin not in json.dumps(context)
            await flow.decide(owner, review['request_id'], review['revision'], 'confirm', NOW)
            assert store.get_snapshot(owner)['pool'] == 4500
    finally:
        await client.close()


async def test_service_nlp_correction_requires_owner_selection_and_preserves_audit(store, actors, monkeypatch):
    _, admin, _, owner = actors
    seed(store, owner)
    seed(store, admin)
    saved = commit(store, owner, [expense()])
    foreign = commit(store, admin, [expense('9', 'FOREIGN_SYNTHETIC_ONLY')])
    own_id = next(t['id'] for t in saved['snapshot']['transactions'] if t['type'] == 'expense')
    foreign_id = next(t['id'] for t in foreign['snapshot']['transactions'] if t['type'] == 'expense')
    client = sdk_client(monkeypatch, lambda request: sdk_response(interpretation(actions=[
        {'type': 'correct', 'reference': 'Synthetic Metro', 'changes': {'amount_inr': '7'}}])))
    before = store.get_snapshot(owner)
    try:
        flow = workflow(store, client)
        question = await flow.natural_language(owner, 'Correct Synthetic Metro to 7', NOW)
        assert own_id in question['text'] and foreign_id not in question['text']
        assert 'FOREIGN_SYNTHETIC_ONLY' not in question['text']
        rejected = await flow.answer(owner, foreign_id, NOW)
        assert rejected['review'] is None and store.get_snapshot(owner) == before
        review = (await flow.answer(owner, own_id, NOW))['review']
        assert review['actions'][0]['transaction_id'] == own_id
        assert 'reference' not in review['actions'][0]
        assert store.get_snapshot(owner) == before
        await flow.decide(owner, review['request_id'], review['revision'], 'confirm', NOW)
        assert 'Total spent: ₹7.00' in day_view(store, owner, '2026-10-06')['text']
        commit(store, owner, [{'type': 'undo', 'transaction_id': own_id}])
        assert store.spending(owner, date(2026, 10, 1), date(2026, 10, 31))['count'] == 0
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(TransactionRevision).where(
                TransactionRevision.transaction_id == own_id)) == 3
    finally:
        await client.close()


@pytest.mark.parametrize('failure', ['timeout', 'server', 'invalid_schema'])
async def test_service_sdk_failure_is_redacted_and_offline_recovery_works(store, actors, monkeypatch, failure):
    _, _, _, owner = actors
    seed(store, owner)
    calls = []

    def handler(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('SYNTHETIC_PRIVATE_DETAIL', request=request)
        if failure == 'server':
            return httpx.Response(503, json={'error': {'message': 'SYNTHETIC_PRIVATE_DETAIL'}})
        return sdk_response({**interpretation(actions=[expense()]), 'owner_id': 'SYNTHETIC_PRIVATE_DETAIL'})

    client = sdk_client(monkeypatch, handler)
    before = store.get_snapshot(owner)
    try:
        flow = workflow(store, client)
        if failure == 'invalid_schema':
            with pytest.raises(BudgetError) as error:
                await flow.natural_language(owner, 'Record 10 Metro Travel', NOW)
            assert error.value.code == 'invalid_model_output'
            assert 'SYNTHETIC_PRIVATE_DETAIL' not in str(error.value)
        else:
            output = await flow.natural_language(owner, 'Record 10 Metro Travel', NOW)
            assert 'unavailable' in output['text'] and output['review'] is None
            assert 'SYNTHETIC_PRIVATE_DETAIL' not in output['text']
        assert len(calls) == 1 and store.get_snapshot(owner) == before
        assert store.get_pending(owner) is None
        offline = workflow(store)
        review = (await offline.submit(owner, [expense()], NOW))['review']
        result = await offline.decide(owner, review['request_id'], review['revision'], 'confirm', NOW)
        assert result['result']['snapshot']['buckets']['Travel']['balance'] == 4000
    finally:
        await client.close()


def setup_state(onboarding, owner):
    with onboarding.store.engine.connect() as connection:
        return onboarding._load(connection, owner)


async def setup_review(app, actor, opening='100', allocation='50'):
    for text in ('/start', opening, 'Travel', allocation):
        reply = (await app.handle(message(actor, text), NOW))[0]
    return (await app.handle(callback(actor, setup_button(reply, 'finish')), NOW))[0]


@pytest.mark.parametrize('action', ['back', 'cancel', 'finish', 'more'])
async def test_generated_setup_buttons_fence_foreign_and_cancelled_drafts(store, actors, onboarding, action):
    other_actor, other, actor, owner = actors
    app = controller(store, onboarding)
    initial = store.get_snapshot(owner)
    for text in ('/start', '100', 'Travel', '50'):
        reply = (await app.handle(message(actor, text), NOW))[0]
    old = setup_button(reply, action)
    await app.handle(message(other_actor, '/start'), NOW)
    foreign_state = setup_state(onboarding, other)
    foreign_reply = (await app.handle(callback(other_actor, old), NOW))[0]
    assert 'no longer current' in foreign_reply['text']
    assert setup_state(onboarding, other) == foreign_state
    await app.handle(callback(actor, setup_button(reply, 'cancel')), NOW)
    tombstone = setup_state(onboarding, owner)
    assert tombstone['step'] == 'cancelled' and not onboarding.active(owner)
    for data in (old, 'cal:2026-10', 'day:2026-10-06'):
        rejected = (await app.handle(callback(actor, data), NOW))[0]
        assert 'setup' in rejected['text'].lower()
        assert setup_state(onboarding, owner) == tombstone
        assert store.get_snapshot(owner) == initial and store.get_pending(owner) is None
    newer = await setup_review(app, actor, '80', '30')
    pending = store.get_pending(owner)
    new_state = setup_state(onboarding, owner)
    await app.handle(callback(actor, old), NOW)
    assert setup_state(onboarding, owner) == new_state
    assert_same_review(store.get_pending(owner), pending)
    await app.handle(callback(actor, review_button(newer, 'confirm')), NOW)
    assert store.get_snapshot(owner)['pool'] == 5000
    assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 3000


@pytest.mark.parametrize('decision', ['confirm', 'edit', 'cancel'])
async def test_setup_review_foreign_and_stale_replay_cannot_change_new_review(store, actors, onboarding, decision):
    other_actor, other, actor, owner = actors
    app = controller(store, onboarding)
    reply = await setup_review(app, actor)
    old = review_button(reply, decision)
    pending, before = store.get_pending(owner), store.get_snapshot(owner)
    other_before = store.get_snapshot(other)
    await app.handle(message(other_actor, '/start'), NOW)
    foreign_state = setup_state(onboarding, other)
    denied = (await app.handle(callback(other_actor, old), NOW))[0]
    assert 'INR 100.00' not in denied['text'] and pending['request_id'] not in denied['text']
    assert_same_review(store.get_pending(owner), pending)
    assert setup_state(onboarding, other) == foreign_state
    assert store.get_snapshot(other) == other_before and store.get_snapshot(owner) == before
    await app.handle(callback(actor, review_button(reply, 'cancel')), NOW)
    newer = await setup_review(app, actor, '80', '30')
    new_pending, new_state = store.get_pending(owner), setup_state(onboarding, owner)
    await app.handle(callback(actor, old), NOW)
    assert_same_review(store.get_pending(owner), new_pending)
    assert setup_state(onboarding, owner) == new_state and store.get_snapshot(owner) == before
    await app.handle(callback(actor, review_button(newer, 'confirm')), NOW)
    assert store.get_snapshot(owner)['pool'] == 5000


@pytest.mark.parametrize('report', ['calendar', 'spending'])
async def test_application_durable_report_clarification_original_receipt(
        store, actors, onboarding, monkeypatch, report):
    _, _, actor, owner = actors
    seed(store, owner)
    commit(store, owner, [expense()])
    before = store.get_snapshot(owner)
    received = datetime(2026, 10, 31, 18, 29, tzinfo=timezone.utc)
    answered = received + timedelta(minutes=2)
    responses = iter([
        {**interpretation('clarification'), 'missing_fields': ['period'],
         'clarification_question': 'Which period?'},
        interpretation('query', query={'report': report, 'period': 'month',
                                      'start': None, 'end': None, 'bucket_name': None}),
    ])
    wire = []

    def respond(request):
        wire.append(json.loads(request.content))
        return sdk_response(next(responses))

    client = sdk_client(monkeypatch, respond)
    try:
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            app = controller(store, onboarding, workflow(store, client, saver))
            question = (await app.handle(message(actor, 'Show my spending calendar', at=received), received))[0]
            assert 'reporting period' in question['text']
            assert not question['keyboard'] and store.get_snapshot(owner) == before
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            app = controller(store, onboarding, workflow(store, client, saver))
            result = (await app.handle(message(actor, 'this month', at=answered), answered))[0]
            if report == 'calendar':
                assert result['text'] == 'October 2026' and 'cal:2026-11' in buttons(result)
            else:
                assert '2026-10-01' in result['text'] and '2026-10-31' in result['text']
                assert '₹10.00' in result['text'] and 'Travel' in result['text']
            assert store.get_snapshot(owner) == before and store.get_pending(owner) is None
        assert len(wire) == 2
        for body in wire:
            context = json.loads(body['input'][0]['content'])
            assert context['local_date'] == '2026-10-31'
            assert set(context) == {'message', 'bucket_names', 'local_date', 'timezone'}
            assert body['store'] is False and str(actor) not in json.dumps(context)
    finally:
        await client.close()


async def test_application_nl_correction_selection_restart_undo_and_readonly_reports(
        store, actors, onboarding, monkeypatch):
    _, other, actor, owner = actors
    seed(store, owner)
    seed(store, other)
    foreign = commit(store, other, [expense('99', 'FOREIGN_SYNTHETIC_ONLY')])
    foreign_id = next(t['id'] for t in foreign['snapshot']['transactions'] if t['type'] == 'expense')
    responses = iter([
        interpretation(actions=[expense()]),
        interpretation(actions=[{'type': 'correct', 'reference': 'Metro', 'changes': {'amount_inr': '7'}}]),
        interpretation(actions=[{'type': 'undo', 'reference': 'Metro'}]),
    ])
    client = sdk_client(monkeypatch, lambda request: sdk_response(next(responses)))
    try:
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            app = controller(store, onboarding, workflow(store, client, saver))
            before = store.get_snapshot(owner)
            review = (await app.handle(message(actor, 'Record 10 for Metro in Travel'), NOW))[0]
            assert store.get_snapshot(owner) == before
            for decision in ('confirm', 'edit', 'cancel'):
                review_button(review, decision)
            confirm_data = review_button(review, 'confirm')
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            app = controller(store, onboarding, workflow(store, client, saver))
            saved = await app.handle(callback(actor, confirm_data), NOW)
            assert await app.handle(callback(actor, confirm_data), NOW) == saved
            snapshot = store.get_snapshot(owner)
            assert snapshot['buckets']['Travel']['balance'] == 4000 and snapshot['pool'] == 5000
            own_id = next(t['id'] for t in snapshot['transactions'] if t['type'] == 'expense')
            selection = (await app.handle(message(actor, 'Correct Metro to 7'), NOW))[0]
            assert own_id in selection['text'] and foreign_id not in selection['text']
            assert 'FOREIGN_SYNTHETIC_ONLY' not in selection['text'] and not selection['keyboard']
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            app = controller(store, onboarding, workflow(store, client, saver))
            rejected = (await app.handle(message(actor, foreign_id), NOW))[0]
            assert 'owner-scoped' in rejected['text'] and not rejected['keyboard']
            assert store.get_snapshot(owner) == snapshot
            review = (await app.handle(message(actor, own_id), NOW))[0]
            assert store.get_pending(owner)['actions'][0]['transaction_id'] == own_id
            assert store.get_snapshot(owner) == snapshot
            await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
            corrected = store.get_snapshot(owner)
            assert corrected['buckets']['Travel']['balance'] == 4300
            for payload in (message(actor, '/spending today'), callback(actor, 'day:2026-10-06')):
                report = (await app.handle(payload, NOW))[0]
                assert '₹7.00' in report['text'] and 'FOREIGN_SYNTHETIC_ONLY' not in report['text']
                assert store.get_snapshot(owner) == corrected
            selection = (await app.handle(message(actor, 'Undo Metro'), NOW))[0]
            assert own_id in selection['text'] and not selection['keyboard']
            review = (await app.handle(message(actor, own_id), NOW))[0]
            assert store.get_snapshot(owner) == corrected
            await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
            assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 5000
            assert store.spending(owner, NOW.date(), NOW.date())['count'] == 0
            with store.engine.connect() as connection:
                rows = connection.execute(select(TransactionRevision.revision, TransactionRevision.amount,
                                                 TransactionRevision.state, TransactionRevision.previous).where(
                    TransactionRevision.transaction_id == own_id).order_by(TransactionRevision.revision)).all()
            assert [r.revision for r in rows] == [1, 2, 3]
            assert [r.amount for r in rows[:2]] == [1000, 700]
            assert rows[1].previous == rows[0].state and rows[2].previous == rows[1].state
            assert not rows[2].state['active']
    finally:
        await client.close()


class TelegramWire(BaseRequest):
    """Controlled network only; Bot parsing/serialization and application are real."""

    def __init__(self):
        self.bot_id = synthetic_id()
        self.updates = []
        self.calls = []
        self.fail_next_send = False

    @property
    def read_timeout(self):
        return 1

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit('/', 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((name, params))
        if name == 'getMe':
            result = {'id': self.bot_id, 'is_bot': True, 'first_name': 'Synthetic', 'username': 'R4TestBot'}
        elif name == 'getUpdates':
            result = self.updates
        elif name == 'answerCallbackQuery':
            result = True
        elif name == 'sendMessage':
            if self.fail_next_send:
                self.fail_next_send = False
                raise TimedOut('SYNTHETIC_TRANSPORT_PRIVATE')
            result = {'message_id': 1, 'date': int(NOW.timestamp()),
                      'chat': {'id': params['chat_id'], 'type': 'private'}, 'text': params['text']}
        else:
            raise AssertionError('Unexpected controlled Telegram method: ' + name)
        return 200, json.dumps({'ok': True, 'result': result}).encode()


def outbox_rows(store, bot_id):
    with store.engine.connect() as connection:
        return list(connection.execute(select(Outbox.__table__).where(
            Outbox.bot_id == str(bot_id)).order_by(Outbox.update_id, Outbox.position)).mappings())


def crash_before_completion(*args, **kwargs):
    raise RuntimeError('SYNTHETIC_DATABASE_PRIVATE')


async def test_application_sdk_transport_duplicate_confirm_crash_retry_and_bot_scope(
        store, actors, onboarding, monkeypatch, caplog):
    _, _, actor, owner = actors
    seed(store, owner)
    wire, app = TelegramWire(), controller(store, onboarding)
    original = message(actor, '/expense 10 Travel Metro')
    original['update_id'] = 10
    wire.updates = [original, original]
    other_bot = synthetic_id()
    store.save_update(other_bot, 10, message(actor, '/balance'), NOW)
    async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
        transport = TelegramTransport(bot, store, app)
        assert await transport.poll_once(NOW) == 1
        assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 5000
        rows = outbox_rows(store, bot.id)
        assert len(rows) == 1
        data = review_button(rows[0], 'confirm')
        confirmation = callback(actor, data)
        confirmation['update_id'] = 12
        wire.updates = [confirmation, confirmation]
        with monkeypatch.context() as fault:
            fault.setattr(store, 'complete_update', crash_before_completion)
            assert await transport.poll_once(NOW) == 0
        assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 4000
        assert store.polling_offset(bot.id) == 13
        assert len(store.pending_updates(bot_id=bot.id)) == 1
        wire.updates = []
        assert await TelegramTransport(bot, store, controller(store, onboarding)).poll_once(NOW) == 1
        assert not store.pending_updates(bot_id=bot.id)
        assert len(store.pending_updates(bot_id=other_bot)) == 1
        assert store.spending(owner, NOW.date(), NOW.date())['count'] == 1
        assert len(outbox_rows(store, bot.id)) == 2
        assert await transport.deliver_once(NOW) == 2
        sent = [params for name, params in wire.calls if name == 'sendMessage']
        assert len(sent) == 2 and 'Recorded.' in sent[1]['text']
        assert all(params['chat_id'] == actor for params in sent)
        assert all(row['delivered_at'] is not None for row in outbox_rows(store, bot.id))
        assert 'SYNTHETIC_DATABASE_PRIVATE' not in caplog.text


async def test_application_provider_outage_commands_and_retry_errors(store, actors, onboarding, monkeypatch, caplog):
    _, _, actor, owner = actors
    seed(store, owner)
    calls = []

    def unavailable(request):
        calls.append(request)
        raise httpx.ReadTimeout('SYNTHETIC_PROVIDER_PRIVATE', request=request)

    client = sdk_client(monkeypatch, unavailable)
    try:
        app = controller(store, onboarding, workflow(store, client))
        initial = store.get_snapshot(owner)
        output = (await app.handle(message(actor, 'Record 10 Metro in Travel'), NOW))[0]
        assert 'unavailable' in output['text'] and not output['keyboard'] and len(calls) == 1
        assert store.get_snapshot(owner) == initial and store.get_pending(owner) is None
        review = (await app.handle(message(actor, '/expense 10 Travel Metro'), NOW))[0]
        await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
        before = store.get_snapshot(owner)
        for payload in (message(actor, '/balance'), message(actor, '/spending today'),
                        message(actor, '/calendar'), callback(actor, 'day:2026-10-06')):
            assert (await app.handle(payload, NOW))[0]['text']
            assert store.get_snapshot(owner) == before
        assert len(calls) == 1
        wire = TelegramWire()
        wire.updates = [message(actor, '/spending today')]
        async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
            transport = TelegramTransport(bot, store, app)

            def broken_report(*args, **kwargs):
                raise RuntimeError('SYNTHETIC_REPORT_PRIVATE')

            with monkeypatch.context() as fault:
                fault.setattr(app.reports, 'spending', broken_report)
                assert await transport.poll_once(NOW) == 0
            assert len(store.pending_updates(bot_id=bot.id)) == 1 and not outbox_rows(store, bot.id)
            wire.updates = []
            assert await transport.poll_once(NOW + timedelta(seconds=1)) == 1
            assert '₹10.00' in outbox_rows(store, bot.id)[0]['text']
        assert 'SYNTHETIC_REPORT_PRIVATE' not in caplog.text
        assert 'SYNTHETIC_PROVIDER_PRIVATE' not in output['text']
    finally:
        await client.close()


async def test_transport_reply_order_claim_limit_backoff_and_fencing(store, actors, onboarding, caplog):
    _, _, actor, owner = actors
    seed(store, owner)
    wire = TelegramWire()
    async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
        app = controller(store, onboarding)
        transport = TelegramTransport(bot, store, app)
        # Real application report replies, persisted together to exercise the 20-item claim boundary.
        replies = []
        for index in range(25):
            reply = (await app.handle(message(actor, '/balance' if index % 2 else '/calendar'), NOW))[0]
            replies.append(reply)
        payload = message(actor, '/balance')
        store.save_update(bot.id, payload['update_id'], payload, NOW)
        store.complete_update(payload['update_id'], replies, NOW, bot_id=bot.id)
        wire.fail_next_send = True
        assert await transport.deliver_once(NOW) == 19
        # A failed first send is delayed, while later replies can already be delivered.
        rows = outbox_rows(store, bot.id)
        assert rows[0]['delivered_at'] is None
        assert all(row['delivered_at'] is not None for row in rows[1:20])
        assert await transport.deliver_once(NOW) == 5
        assert [p['text'] for name, p in wire.calls if name == 'sendMessage'] == [r['text'] for r in replies]
        later = NOW + timedelta(seconds=3)
        lease = store.pending_outbox(later, bot_id=bot.id)
        assert len(lease) == 1 and lease[0]['text'] == replies[0]['text']
        assert store.ack_outbox(lease[0]['id'], str(uuid4()), later) is False
        assert store.fail_outbox(lease[0]['id'], lease[0]['lease_token'], later) is True
        assert store.ack_outbox(lease[0]['id'], lease[0]['lease_token'], later) is False
        assert await transport.deliver_once(NOW + timedelta(seconds=8)) == 1
        assert all(row['delivered_at'] is not None for row in outbox_rows(store, bot.id))
        assert 'SYNTHETIC_TRANSPORT_PRIVATE' not in caplog.text


async def test_defect_setup_answer_replay_must_not_answer_next_question(store, actors, onboarding, monkeypatch):
    """Strict recovery gate: crash after draft save but before inbox completion."""
    _, _, actor, owner = actors
    app, wire = controller(store, onboarding), TelegramWire()
    await app.handle(message(actor, '/start'), NOW)
    wire.updates = [message(actor, '100')]
    async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
        transport = TelegramTransport(bot, store, app)
        with monkeypatch.context() as fault:
            fault.setattr(store, 'complete_update', crash_before_completion)
            assert await transport.poll_once(NOW) == 0
        saved = setup_state(onboarding, owner)
        assert saved['step'] == 'name' and saved['opening'] == 10000
        before = store.get_snapshot(owner)
        wire.updates = []
        assert await transport.poll_once(NOW) == 1
        assert store.get_snapshot(owner) == before and store.get_pending(owner) is None
        assert setup_state(onboarding, owner) == saved, 'Same inbox answer must not become a bucket name'


async def test_defect_proposal_retry_must_return_original_review_buttons(store, actors, onboarding, monkeypatch):
    """Strict recovery gate: persisted proposal with no durable review reply yet."""
    _, _, actor, owner = actors
    seed(store, owner)
    wire, app = TelegramWire(), controller(store, onboarding)
    wire.updates = [message(actor, '/expense 10 Travel Metro')]
    async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
        transport = TelegramTransport(bot, store, app)
        before = store.get_snapshot(owner)
        with monkeypatch.context() as fault:
            fault.setattr(store, 'complete_update', crash_before_completion)
            assert await transport.poll_once(NOW) == 0
        saved = store.get_pending(owner)
        assert saved is not None and not outbox_rows(store, bot.id)
        wire.updates = []
        assert await transport.poll_once(NOW) == 1
        assert_same_review(store.get_pending(owner), saved)
        assert store.get_snapshot(owner) == before
        reply = outbox_rows(store, bot.id)[0]
        for decision in ('confirm', 'edit', 'cancel'):
            assert review_button(reply, decision) == f'rev:{saved["request_id"]}:{saved["revision"]}:{decision}'


async def test_defect_sdk_parse_failure_must_not_log_private_update(store, actors, onboarding, caplog):
    """Privacy gate on the real SDK failure path; never use real private content."""
    _, _, actor, owner = actors
    seed(store, owner)
    wire = TelegramWire()
    payload = callback(actor, 'SYNTHETIC_PRIVATE_CALLBACK_CONTENT')
    del payload['callback_query']['chat_instance']
    wire.updates = [payload]
    before = store.get_snapshot(owner)
    async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
        transport = TelegramTransport(bot, store, controller(store, onboarding))
        with pytest.raises(TypeError):
            await transport.poll_once(NOW)
        assert store.polling_offset(bot.id) == 0 and not store.pending_updates(bot_id=bot.id)
        assert store.get_snapshot(owner) == before
    assert 'SYNTHETIC_PRIVATE_CALLBACK_CONTENT' not in caplog.text, 'SDK parse errors must not expose update content'


async def test_inbox_has_no_worker_claim_and_later_update_can_overtake_failure(
        store, actors, onboarding, monkeypatch):
    """Characterize a real limit, NOT acceptance of exactly-once or strict order."""
    _, _, actor, owner = actors
    seed(store, owner)
    wire, app = TelegramWire(), controller(store, onboarding)
    first, second = message(actor, '/spending today'), message(actor, '/balance')
    first['update_id'], second['update_id'] = 10, 12
    wire.updates = [first, second]
    async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
        store.save_update(bot.id, 10, first, NOW)
        # Independent consumers can read the exact same pending item; no claim token exists.
        a = store.pending_updates(bot_id=bot.id)
        b = store.pending_updates(bot_id=bot.id)
        assert a == b and len(a) == 1 and 'lease_token' not in a[0]
        transport = TelegramTransport(bot, store, app)
        with monkeypatch.context() as fault:
            fault.setattr(app.reports, 'spending', crash_before_completion)
            assert await transport.poll_once(NOW) == 1
        assert [row['update_id'] for row in store.pending_updates(bot_id=bot.id)] == [10]
        assert [row['update_id'] for row in outbox_rows(store, bot.id)] == [12]
        assert store.polling_offset(bot.id) == 13
        wire.updates = []
        assert await transport.poll_once(NOW + timedelta(seconds=1)) == 1
        assert [row['update_id'] for row in outbox_rows(store, bot.id)] == [10, 12]


@pytest.mark.parametrize('decision', ['confirm', 'edit', 'cancel'])
async def test_revocation_blocks_pending_review_without_financial_change(store, actors, onboarding, decision):
    admin_actor, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    review = (await app.handle(message(actor, '/expense 10 Travel Metro'), NOW))[0]
    pending = store.get_pending(owner)
    with store.engine.connect() as connection:
        before = connection.execute(select(Account.id, Account.balance).where(
            Account.owner_id == owner).order_by(Account.id)).all()
    await app.handle(message(admin_actor, '/revoke ' + str(actor)), NOW)
    rejected = (await app.handle(callback(actor, review_button(review, decision)), NOW))[0]
    assert rejected['text'] == 'Your access has been revoked. Contact the administrator.'
    assert not rejected['keyboard'] and 'Travel' not in rejected['text']
    with store.engine.connect() as connection:
        assert connection.execute(select(Account.id, Account.balance).where(
            Account.owner_id == owner).order_by(Account.id)).all() == before
        assert connection.scalar(select(Request.status).where(
            Request.owner_id == owner, Request.id == pending['request_id'])) == 'pending'
        assert connection.scalar(select(func.count()).select_from(Batch).where(Batch.owner_id == owner)) == 1
    fresh = store.create_invite(store.get_user(admin_actor)['owner_id'], NOW)
    again = (await app.handle(message(actor, '/start ' + fresh), NOW))[0]
    assert 'revoked' in again['text'] and not store.get_user(actor)['active']


async def test_application_edit_rebuilds_review_and_stale_controls_do_not_commit(
        store, actors, onboarding, monkeypatch):
    _, _, actor, owner = actors
    seed(store, owner)
    client = sdk_client(monkeypatch, lambda request: sdk_response(interpretation(actions=[expense('7')])))
    try:
        app = controller(store, onboarding, workflow(store, client))
        initial = store.get_snapshot(owner)
        original = (await app.handle(message(actor, '/expense 10 Travel Metro'), NOW))[0]
        old = store.get_pending(owner)
        question = (await app.handle(callback(actor, review_button(original, 'edit')), NOW))[0]
        assert 'replacement' in question['text'] and not question['keyboard']
        replacement = (await app.handle(message(actor, 'Make the Metro expense 7 in Travel'), NOW))[0]
        newer = store.get_pending(owner)
        assert newer['request_id'] == old['request_id'] and newer['revision'] > old['revision']
        assert newer['actions'][0]['amount_inr'] == '7.00' and 'INR 7.00' in replacement['text']
        for decision in ('edit', 'cancel'):
            await app.handle(callback(actor, review_button(original, decision)), NOW)
            assert_same_review(store.get_pending(owner), newer)
        renewed = (await app.handle(callback(actor, review_button(original, 'confirm')), NOW))[0]
        assert store.get_snapshot(owner) == initial
        assert store.get_pending(owner)['revision'] > newer['revision']
        await app.handle(callback(actor, review_button(renewed, 'confirm')), NOW)
        assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 4300
        assert store.spending(owner, NOW.date(), NOW.date())['total'] == 700
    finally:
        await client.close()


async def test_application_combined_income_allocation_expense_atomic_and_target_warning(
        store, actors, onboarding, monkeypatch):
    _, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    target = (await app.handle(message(actor, '/target Travel 5'), NOW))[0]
    assert store.get_snapshot(owner)['buckets']['Travel']['target'] is None
    await app.handle(callback(actor, review_button(target, 'confirm')), NOW)
    actions = [{'type': 'income', 'amount_inr': '20', 'description': 'Synthetic income'},
               {'type': 'allocate', 'amount_inr': '20', 'bucket_name': 'Travel'}, expense('80')]
    client = sdk_client(monkeypatch, lambda request: sdk_response(interpretation(actions=actions)))
    try:
        app = controller(store, onboarding, workflow(store, client))
        before = store.get_snapshot(owner)
        review = (await app.handle(message(actor, 'Income 20, allocate 20 to Travel, spend 80 Metro'), NOW))[0]
        assert len(store.get_pending(owner)['actions']) == 3
        assert store.get_snapshot(owner) == before
        assert 'negative' in review['text'].lower() and 'target' in review['text'].lower()
        await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
        snapshot = store.get_snapshot(owner)
        assert snapshot['pool'] == 5000 and snapshot['buckets']['Travel']['balance'] == -1000
        assert store.spending(owner, NOW.date(), NOW.date())['total'] == 8000
        txs = snapshot['transactions'][-3:]
        assert [t['type'] for t in txs] == ['income', 'allocate', 'expense']
        assert len({t['batch_id'] for t in txs}) == 1
    finally:
        await client.close()


async def test_application_reversal_shortage_and_future_expense_reject_without_review(store, actors, onboarding):
    _, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    review = (await app.handle(message(actor, '/income 10 Synthetic'), NOW))[0]
    await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
    income_id = next(t['id'] for t in store.get_snapshot(owner)['transactions'] if t['type'] == 'income')
    review = (await app.handle(message(actor, '/allocate 60 Travel'), NOW))[0]
    await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
    before = store.get_snapshot(owner)
    for command in ('/undo ' + income_id, '/correct ' + income_id + ' amount=5'):
        reply = (await app.handle(message(actor, command), NOW))[0]
        assert 'pool has ₹0.00' in reply['text'] and 'restore' in reply['text'] and 'to cover' in reply['text']
        assert not reply['keyboard'] and store.get_snapshot(owner) == before
        assert store.get_pending(owner) is None
    reply = (await app.handle(message(actor, '/expense 1 Travel Future 2026-10-07'), NOW))[0]
    assert 'future' in reply['text'].lower() and not reply['keyboard']
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None


async def test_application_expired_review_cannot_commit(store, actors, onboarding):
    _, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    review = (await app.handle(message(actor, '/expense 10 Travel Metro'), NOW))[0]
    saved, before = store.get_pending(owner), store.get_snapshot(owner)
    expired = (await app.handle(callback(actor, review_button(review, 'confirm')), NOW + timedelta(minutes=31)))[0]
    assert 'expired' in expired['text'].lower() and store.get_snapshot(owner) == before
    assert_same_review(store.get_pending(owner), saved)


async def test_sdk_invite_guided_setup_durable_confirm_and_calendar(store, actors, onboarding):
    _, admin, _, _ = actors
    actor = synthetic_id()
    invite = store.create_invite(admin, NOW)
    wire = TelegramWire()
    async with Bot('123:SYNTHETIC_R4', request=wire, get_updates_request=wire) as bot:
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            app = controller(store, onboarding, workflow(store, saver=saver))
            transport = TelegramTransport(bot, store, app)
            for update_id, text in enumerate(('/start ' + invite, '100', 'Travel', '50'), start=1):
                payload = message(actor, text)
                payload['update_id'] = update_id
                wire.updates = [payload]
                assert await transport.poll_once(NOW) == 1
                assert await transport.deliver_once(NOW) == 1
            owner = store.get_user(actor)['owner_id']
            assert not store.get_snapshot(owner)['onboarded'] and store.get_snapshot(owner)['pool'] == 0
            reply = outbox_rows(store, bot.id)[-1]
            payload = callback(actor, setup_button(reply, 'finish'))
            payload['update_id'] = 5
            wire.updates = [payload]
            assert await transport.poll_once(NOW) == 1
            assert await transport.deliver_once(NOW) == 1
            reply = outbox_rows(store, bot.id)[-1]
            confirm_data = review_button(reply, 'confirm')
            assert store.get_snapshot(owner)['pool'] == 0
        async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w8') as saver:
            app = controller(store, onboarding, workflow(store, saver=saver))
            transport = TelegramTransport(bot, store, app)
            payload = callback(actor, confirm_data)
            payload['update_id'] = 6
            wire.updates = [payload, payload]
            assert await transport.poll_once(NOW) == 1
            assert await transport.deliver_once(NOW) == 1
            snapshot = store.get_snapshot(owner)
            assert snapshot['onboarded'] and snapshot['pool'] == 5000
            assert snapshot['buckets']['Travel']['balance'] == 5000
            payload = message(actor, '/calendar')
            payload['update_id'] = 7
            wire.updates = [payload]
            assert await transport.poll_once(NOW) == 1
            assert await transport.deliver_once(NOW) == 1
            assert outbox_rows(store, bot.id)[-1]['text'] == 'October 2026'
            assert store.get_snapshot(owner) == snapshot and store.polling_offset(bot.id) == 8
            assert {name for name, _ in wire.calls} <= {'getMe', 'getUpdates', 'sendMessage', 'answerCallbackQuery'}


async def test_application_transaction_failure_rolls_back_entire_batch_then_retries(
        store, actors, onboarding, monkeypatch):
    _, _, actor, owner = actors
    seed(store, owner)
    actions = [{'type': 'income', 'amount_inr': '5', 'description': 'Synthetic'},
               {'type': 'allocate', 'amount_inr': '5', 'bucket_name': 'Travel'}, expense('5')]
    client = sdk_client(monkeypatch, lambda request: sdk_response(interpretation(actions=actions)))
    try:
        app = controller(store, onboarding, workflow(store, client))
        before = store.get_snapshot(owner)
        review = (await app.handle(message(actor, 'Income 5, allocate 5 Travel, spend 5 Metro'), NOW))[0]
        pending = store.get_pending(owner)
        real_commit = store._commit

        def fail_after_writes(*args, **kwargs):
            real_commit(*args, **kwargs)
            raise RuntimeError('Synthetic transaction fault')

        with monkeypatch.context() as fault:
            fault.setattr(store, '_commit', fail_after_writes)
            with pytest.raises(RuntimeError, match='Synthetic transaction fault'):
                await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
        assert store.get_snapshot(owner) == before
        assert_same_review(store.get_pending(owner), pending)
        with store.engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(Batch).where(Batch.owner_id == owner)) == 1
        await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
        assert store.get_snapshot(owner)['pool'] == 5000
        assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 5000
        assert store.spending(owner, NOW.date(), NOW.date())['total'] == 500
    finally:
        await client.close()


async def test_application_missing_bucket_requires_selection_and_no_implicit_creation(
        store, actors, onboarding, monkeypatch):
    _, _, actor, owner = actors
    seed(store, owner)
    # The SDK schema requires complete expense actions: missing bucket is a
    # clarification envelope, not an invalid partial mutation passed around it.
    clarification = {**interpretation('clarification'), 'missing_fields': ['bucket_name'],
                     'clarification_question': 'Choose a bucket'}
    outputs = iter([clarification, clarification, interpretation(actions=[expense('4')])])
    client = sdk_client(monkeypatch, lambda request: sdk_response(next(outputs)))
    try:
        app = controller(store, onboarding, workflow(store, client))
        before = store.get_snapshot(owner)
        question = (await app.handle(message(actor, 'Spent 4 Metro'), NOW))[0]
        assert 'bucket' in question['text'].lower() and 'Travel' in question['text'] and not question['keyboard']
        rejected = (await app.handle(message(actor, 'Unknown bucket'), NOW))[0]
        assert not rejected['keyboard'] and store.get_snapshot(owner) == before
        assert store.get_pending(owner) is None
        review = (await app.handle(message(actor, 'Travel'), NOW))[0]
        assert 'INR 4.00' in review['text']
        assert store.get_snapshot(owner) == before
        await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
        assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 4600
        assert set(store.get_snapshot(owner)['buckets']) == {'Travel'}
    finally:
        await client.close()


async def test_application_transfer_source_shortage_and_calendar_pagination(store, actors, onboarding):
    _, _, actor, owner = actors
    seed(store, owner)
    app = controller(store, onboarding)
    for command in ('/bucket Food', '/transfer 10 Travel Food'):
        before = store.get_snapshot(owner)
        review = (await app.handle(message(actor, command), NOW))[0]
        assert store.get_snapshot(owner) == before
        await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
    before = store.get_snapshot(owner)
    assert before['buckets']['Travel']['balance'] == 4000 and before['buckets']['Food']['balance'] == 1000
    rejected = (await app.handle(message(actor, '/transfer 11 Food Travel'), NOW))[0]
    assert 'restore ₹1.00' in rejected['text'] and store.get_snapshot(owner) == before
    assert store.get_pending(owner) is None
    for index in range(9):
        review = (await app.handle(message(actor, f'/expense 1 Travel Synthetic-{index}'), NOW))[0]
        await app.handle(callback(actor, review_button(review, 'confirm')), NOW)
    before = store.get_snapshot(owner)
    first = (await app.handle(callback(actor, 'day:2026-10-06'), NOW))[0]
    assert first['text'].count('Synthetic-') == 8
    assert 'day:2026-10-06:1' in buttons(first)
    last = (await app.handle(callback(actor, 'day:2026-10-06:1'), NOW))[0]
    assert last['text'].count('Synthetic-') == 1
    assert 'Total spent: ₹9.00' in first['text'] and 'Total spent: ₹9.00' in last['text']
    assert store.get_snapshot(owner) == before


@pytest.mark.parametrize('text', ['/expense', '/income 1e3 Salary', '/allocate 1.001 Travel', '/timezone Bad/Zone'])
async def test_input_validation_safe_no_write(store, actors, onboarding, text):
    _, _, actor, owner = actors
    seed(store, owner)
    before = store.get_snapshot(owner)
    reply = (await controller(store, onboarding).handle(message(actor, text), NOW))[0]
    assert '/help' in reply['text'] and not reply['keyboard']
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None
