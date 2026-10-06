"""Independent release gates: real test_w8 state, no live Telegram/provider calls.

Ingress tests deliberately fail on missing frozen-contract wiring; no xfails/skips.
Service tests separately establish component evidence, not runnable release status.
"""
import importlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

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
from budget_bot.storage.models import Batch, Outbox, TransactionRevision
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
    payload['callback_query'] = {'id': str(uuid4()), 'from': msg['from'], 'message': msg, 'data': data}
    return payload


def buttons(output):
    return [button['data'] for row in output['keyboard'] for button in row]


def review_button(reply, decision):
    selected = [data for data in buttons(reply) if data.startswith('rev:') and data.endswith(':' + decision)]
    assert len(selected) == 1, 'Review must expose one owner-bound ' + decision + ' button'
    return selected[0]


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
    reply = (await app.handle(callback(actor, 'setup:finish'), NOW))[0]
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
                await app.handle(message(actor, value), NOW)
            new_reply = (await app.handle(callback(actor, 'setup:finish'), NOW))[0]
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
    assert store.get_pending(admin) == review


async def test_ingress_invalid_finance_is_safe_and_no_write(store, actors, onboarding):
    _, _, actor, owner = actors
    seed(store, owner)
    before = store.get_snapshot(owner)
    try:
        reply = (await controller(store, onboarding).handle(message(actor, '/allocate 51 Travel'), NOW))[0]
    except BudgetError as error:
        pytest.fail('Planner validation must become safe reply; escaped code=' + error.code, pytrace=False)
    assert 'fund' in reply['text'].lower() or 'available' in reply['text'].lower()
    assert store.get_snapshot(owner) == before and store.get_pending(owner) is None


async def test_ingress_optional_conversation_injection_contract(store, onboarding):
    class UnsupportedConversation:
        async def dispatch(self, owner_id, text, received_at, now, *, query=None, callback_data=None):
            return None

        def menu(self):
            return {'text': 'Synthetic menu seam', 'keyboard': []}

    try:
        BudgetController(store, workflow(store), onboarding, ReportService(store), AccessService(store),
                         conversation=UnsupportedConversation())
    except TypeError:
        pytest.fail('Missing keyword-only BudgetController(..., conversation=None) seam', pytrace=False)


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
        assert 'invite-only' in reply[0]['text']
        assert 'Travel' not in reply[0]['text'] and '₹' not in reply[0]['text']
    else:
        assert reply == []
    if actor_mode != 'revoked':
        assert store.get_snapshot(owner) == before


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
