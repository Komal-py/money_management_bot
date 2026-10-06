"""R2 targeted ingress tests; only the frozen conversation boundary is doubled."""
import os
from uuid import uuid4

import pytest

from budget_bot.controller import BudgetController
from budget_bot.services.access import AccessService
from budget_bot.services.onboarding import OnboardingService
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.workflows import BudgetWorkflow
from test_controller import NOW, Store, Workflow, message


class Conversation:
    def __init__(self):
        self.calls = []

    async def dispatch(self, owner_id, text, received_at, now, *, query=None, callback_data=None):
        self.calls.append((owner_id, text, received_at, now, query, callback_data))
        return {'text': 'Conversation boundary', 'keyboard': []}

    def menu(self):
        return {'text': 'Budget menu', 'keyboard': [[{'text': 'Calendar', 'data': 'cal:2026-10'}]]}


@pytest.mark.asyncio
async def test_optional_keyword_only_conversation_is_injected_and_lazy():
    conversation = Conversation()
    controller = BudgetController(Store(), Workflow(), None, None, None, conversation=conversation)
    assert controller.conversation is conversation
    assert BudgetController(Store(), Workflow(), None, None, None).conversation is None
    with pytest.raises(TypeError):
        BudgetController(Store(), Workflow(), None, None, None, None, conversation)


@pytest.fixture(scope='module')
def db():
    assert os.environ.get('BUDGET_TEST_DATABASE_URL'), 'Supply the approved test environment.'
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w2', 'Only test_w2 is authorized.'
    store = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w2')
    store.initialize()
    yield store
    store.close()


@pytest.fixture
def actor(db):
    telegram_id = uuid4().int % (2**52 - 1) + 1
    return telegram_id, db.ensure_admin(telegram_id)


@pytest.fixture
def app(db):
    onboarding = OnboardingService(db)
    onboarding.initialize()
    return BudgetController(db, BudgetWorkflow(db), onboarding, ReportService(db),
                            AccessService(db), conversation=Conversation())


def private(telegram_id, text):
    return message(sender=telegram_id, chat=telegram_id, text=text)


def button(telegram_id, data):
    return {'callback_query': {'id': str(uuid4()), 'from': {'id': telegram_id, 'is_bot': False},
            'message': {'chat': {'id': telegram_id, 'type': 'private'}}, 'data': data}}


def keyboard_data(reply, label):
    return next(item['data'] for row in reply['keyboard'] for item in row if item['text'] == label)


@pytest.mark.asyncio
async def test_invite_redemption_uses_trusted_identity_and_starts_setup(db, actor, app):
    _, admin = actor
    telegram_id = uuid4().int % (2**52 - 1) + 1
    code = db.create_invite(admin, NOW)
    response = await app.handle(private(telegram_id, f'/start {code}'), NOW)
    assert 'opening' in response[0]['text'].lower()
    user = db.get_user(telegram_id)
    assert user['active'] and not user['onboarded']
    assert app.onboarding.active(user['owner_id'])
    assert db.get_snapshot(user['owner_id'])['transactions'] == []
    other_id = uuid4().int % (2**52 - 1) + 1
    response = await app.handle(private(other_id, f'/start {code}'), NOW)
    assert 'invalid' in response[0]['text'].lower()
    assert db.get_user(other_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('template', ['/start "{}"', '/start {} extra', '/START {}',
                                     '/start@otherbot {}', '/start {{{}}}', '/start {}='])
async def test_invites_are_strictly_parsed_without_token_repair(db, actor, app, template):
    code = db.create_invite(actor[1], NOW)
    telegram_id = uuid4().int % (2**52 - 1) + 1
    response = await app.handle(private(telegram_id, template.format(code)), NOW)
    assert 'invite' in response[0]['text'].lower()
    assert db.get_user(telegram_id) is None
    # Malformed presentation must not consume the actual valid invitation.
    assert db.redeem_invite(code, telegram_id, NOW)


@pytest.mark.asyncio
async def test_revoked_owner_cannot_redeem_or_resume(db, actor, app):
    telegram_id = uuid4().int % (2**52 - 1) + 1
    owner = db.redeem_invite(db.create_invite(actor[1], NOW), telegram_id, NOW)
    setup = app.onboarding.handle(owner, '/start', NOW)
    db.revoke_user(actor[1], telegram_id, NOW)
    code = db.create_invite(actor[1], NOW)
    for payload in (private(telegram_id, f'/start {code}'), private(telegram_id, '10'),
                    button(telegram_id, keyboard_data(setup, 'Cancel'))):
        response = await app.handle(payload, NOW)
        assert 'revoked' in response[0]['text'].lower()
    assert db.get_user(telegram_id)['active'] is False
    assert db.redeem_invite(code, uuid4().int % (2**52 - 1) + 1, NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize('identity', [True, 0, -1, '1', 2**63])
async def test_invalid_actor_types_never_reach_store(identity):
    store = Store()
    controller = BudgetController(store, None, None, None, None)
    assert await controller.handle(private(identity, '/help'), NOW) == []
    assert store.calls == []


@pytest.mark.asyncio
async def test_callback_without_own_sender_never_uses_message_sender():
    store = Store()
    controller = BudgetController(store, None, None, None, None)
    payload = {'callback_query': {'data': 'setup:finish', 'message': message()['message']}}
    assert await controller.handle(payload, NOW) == []
    assert store.calls == []


@pytest.mark.asyncio
async def test_callback_chat_is_never_replaced_by_top_level_message():
    store = Store()
    workflow = Workflow()
    controller = BudgetController(store, workflow, None, None, None)
    payload = message()
    payload.update(button(1, 'rev:11111111-1111-4111-8111-111111111111:1:confirm'))
    payload['callback_query']['message']['chat'] = {'id': -100, 'type': 'group'}
    assert await controller.handle(payload, NOW) == []
    assert store.calls == [] and workflow.calls == []
