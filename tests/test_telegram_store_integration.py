"""Real integrated store, isolated test_w4, controlled PTB request only."""
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update

from budget_bot.domain.errors import BudgetError
from budget_bot.storage import BudgetStore
from budget_bot.storage.models import Cursor, Inbox, Outbox
from budget_bot.telegram.commands import parse_command
from budget_bot.telegram.transport import TelegramTransport
from test_telegram_transport import NOW, PAYLOAD, Controller, Request

pytestmark = pytest.mark.postgres


@pytest.fixture
def real_store():
    url = os.environ.get('BUDGET_TEST_DATABASE_URL')
    assert url, 'Coordinator must supply the test database URL in the environment.'
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w4'
    store = None
    try:
        store = BudgetStore(url, schema='test_w4')
        store.initialize()
    except Exception:
        if store:
            store.close()
        pytest.fail('Isolated test_w4 database initialization failed; connection details withheld.', pytrace=False)
    # Only transport tables in the explicitly authorized isolated schema.
    def clean():
        with store.engine.begin() as connection:
            for model in (Outbox, Inbox, Cursor):
                connection.execute(delete(model))
    clean()
    try:
        yield store
    finally:
        clean()
        store.close()


async def test_real_inbox_failure_restart_receipt_and_json_outbox(real_store):
    request, controller = Request(), Controller()
    receipts = []
    fail = True
    text = '₹😀' * 3000
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('Confirm', callback_data='rev:opaque')]])
    expected_payload = Update.de_json(PAYLOAD, None).to_dict()

    async def handle(payload, received_at):
        assert payload == expected_payload
        receipts.append(received_at)
        if fail:
            raise RuntimeError('private data must not be logged')
        return [{'chat_id': 7, 'text': text, 'keyboard': keyboard}]

    controller.handle = handle
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        transport = TelegramTransport(bot, real_store, controller)
        assert await transport.poll_once(NOW) == 0
        pending = real_store.pending_updates()
        assert len(pending) == 1
        assert pending[0]['payload'] == expected_payload
        assert datetime.fromisoformat(pending[0]['received_at']) == NOW
        assert real_store.polling_offset(99) == 11
        fail = False
        request.updates = []
        restarted = TelegramTransport(bot, real_store, controller)
        later = NOW + timedelta(minutes=1)
        assert await restarted.poll_once(later) == 1
        assert receipts == [NOW, NOW]
        assert not real_store.pending_updates()
        with Session(real_store.engine) as session:
            rows = session.scalars(select(Outbox).order_by(Outbox.position)).all()
            assert len(rows) == 3
            assert ''.join(row.text for row in rows) == text
            assert all(row.keyboard is None for row in rows[:-1])
            assert rows[-1].keyboard == keyboard.to_dict()
        assert await restarted.deliver_once(later) == 3
        assert not real_store.pending_outbox(later)
        sends = [params for method, params in request.calls if method == 'sendMessage']
        assert ''.join(item['text'] for item in sends) == text
        assert [item['text'] for item in sends] == [row.text for row in rows]
        assert sum('reply_markup' in item for item in sends) == 1
        assert 'reply_markup' in sends[-1]
        assert [params['offset'] for method, params in request.calls if method == 'getUpdates'] == [0, 11]
        assert not any(method == 'deleteWebhook' for method, _ in request.calls)


async def test_real_gap_cursor_durable_not_handler_completion(real_store):
    request, controller = Request(), Controller()
    controller.fail = True
    request.updates = [PAYLOAD, {**PAYLOAD, 'update_id': 12}]
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        transport = TelegramTransport(bot, real_store, controller)
        assert await transport.poll_once(NOW) == 0
        assert real_store.polling_offset(99) == 13
        assert [row['update_id'] for row in real_store.pending_updates()] == [10, 12]
        request.updates = [{**PAYLOAD, 'update_id': 11}]
        assert await transport.poll_once(NOW) == 0
        assert real_store.polling_offset(99) == 13
        assert len(real_store.pending_updates()) == 3
        offsets = [params['offset'] for method, params in request.calls if method == 'getUpdates']
        assert offsets == [0, 13]


async def test_real_outbox_timeout_backoff_and_fencing(real_store):
    request, controller = Request(), Controller()
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        transport = TelegramTransport(bot, real_store, controller)
        assert await transport.poll_once(NOW) == 1
        request.timeout_send = True
        assert await transport.deliver_once(NOW) == 0
        assert not real_store.pending_outbox(NOW)
        later = NOW + timedelta(seconds=3)
        claimed = real_store.pending_outbox(later)
        assert len(claimed) == 1
        item = claimed[0]
        assert real_store.ack_outbox(item['id'], '00000000-0000-0000-0000-000000000000', later) is False
        assert real_store.fail_outbox(item['id'], item['lease_token'], later) is True
        request.timeout_send = False
        assert await transport.deliver_once(NOW + timedelta(seconds=8)) == 1
        assert not real_store.pending_outbox(NOW + timedelta(seconds=8))


async def test_transport_only_handles_and_delivers_its_bot(real_store):
    request, controller = Request(), Controller()
    request.updates = []
    other_payload = {**PAYLOAD, 'message': {**PAYLOAD['message'], 'text': 'Other bot'}}
    real_store.save_update(100, 10, other_payload, NOW)
    real_store.save_update(99, 10, PAYLOAD, NOW)
    real_store.save_update(100, 11, other_payload, NOW)
    real_store.complete_update(11, [{'chat_id': 888, 'text': 'Other bot outbox'}], NOW, bot_id=100)
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        transport = TelegramTransport(bot, real_store, controller)
        assert await transport.poll_once(NOW) == 1
        assert [row['bot_id'] for row in real_store.pending_updates()] == ['100']
        assert await transport.deliver_once(NOW) == 1
        sends = [params for method, params in request.calls if method == 'sendMessage']
        assert [item['chat_id'] for item in sends] == [7]
        remaining = real_store.pending_outbox(NOW)
        assert len(remaining) == 1
        assert remaining[0]['text'] == 'Other bot outbox'


def test_outbox_claim_preserves_persisted_reply_position(real_store):
    real_store.save_update(99, 10, PAYLOAD, NOW)
    texts = ['First', 'Second', 'Last: review buttons']
    real_store.complete_update(10, [{'chat_id': 7, 'text': text} for text in texts], NOW, bot_id=99)
    with real_store.engine.begin() as connection:
        # Opposing UUID order makes the prior random-ID ordering fail deterministically.
        for position, suffix in enumerate((3, 2, 1)):
            connection.execute(Outbox.__table__.update().where(Outbox.position == position)
                               .values(id=f'00000000-0000-0000-0000-{suffix:012d}'))
    claimed = real_store.pending_outbox(NOW)
    assert [row['text'] for row in claimed] == texts


async def test_reply_order_spans_twenty_item_delivery_claims(real_store):
    request = Request()
    request.updates = []
    texts = [f'Reply part {position:02d}' for position in range(25)]
    keyboard = [[{'text': 'Confirm', 'data': 'rev:opaque'}]]
    real_store.save_update(99, 10, PAYLOAD, NOW)
    replies = [{'chat_id': 7, 'text': text,
                'keyboard': keyboard if position == len(texts) - 1 else None}
               for position, text in enumerate(texts)]
    real_store.complete_update(10, replies, NOW, bot_id=99)
    with real_store.engine.begin() as connection:
        for position in range(len(texts)):
            connection.execute(Outbox.__table__.update().where(Outbox.position == position)
                               .values(id=f'00000000-0000-0000-0000-{len(texts) - position:012d}'))
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        transport = TelegramTransport(bot, real_store, Controller())
        assert await transport.deliver_once(NOW) == 20
        first_sends = [params for method, params in request.calls if method == 'sendMessage']
        assert [item['text'] for item in first_sends] == texts[:20]
        assert not any('reply_markup' in item for item in first_sends)
        assert await transport.deliver_once(NOW) == 5
        sends = [params for method, params in request.calls if method == 'sendMessage']
        assert [item['text'] for item in sends] == texts
        assert 'reply_markup' in sends[-1]
        assert not real_store.pending_outbox(NOW, bot_id=99)


def test_real_command_money_is_only_a_proposal(real_store):
    owner = real_store.ensure_admin(900004)
    before = real_store.get_snapshot(owner)
    command = parse_command('/income 10 Salary', NOW, before['timezone'])
    with pytest.raises(BudgetError) as error:
        real_store.propose(owner, command['actions'], NOW)
    assert error.value.code == 'not_onboarded'
    assert real_store.get_snapshot(owner) == before
    command = parse_command('/bucket Travel', NOW, before['timezone'])
    review = real_store.propose(owner, command['actions'], NOW)
    try:
        assert review['status'] == 'pending'
        assert review['actions'][0]['type'] == 'create_bucket'
        assert real_store.get_snapshot(owner) == before
        assert real_store.get_pending(owner)['request_id'] == review['request_id']
    finally:
        real_store.cancel(owner, review['request_id'], NOW)
