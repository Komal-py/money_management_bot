import json
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import TimedOut
from telegram.request import BaseRequest

from budget_bot.telegram.transport import TelegramTransport, create_bot

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
PAYLOAD = {'update_id': 10, 'message': {'message_id': 1, 'date': 1791244800, 'chat': {'id': 7, 'type': 'private'}, 'from': {'id': 7, 'is_bot': False, 'first_name': 'User'}, 'text': '/balance'}}


class Request(BaseRequest):
    def __init__(self):
        self.calls = []
        self.updates = [PAYLOAD, PAYLOAD]
        self.timeout_send = False

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit('/', 1)[1]
        parameters = request_data.parameters if request_data else {}
        self.calls.append((name, parameters))
        if name == 'getMe':
            result = {'id': 99, 'is_bot': True, 'first_name': 'Budget', 'username': 'BudgetBot'}
        elif name == 'getUpdates':
            result = self.updates
        elif name == 'sendMessage':
            if self.timeout_send:
                raise TimedOut()
            result = {'message_id': 2, 'date': 1791244800, 'chat': {'id': 7, 'type': 'private'}, 'text': parameters['text']}
        else:
            result = True
        return 200, json.dumps({'ok': True, 'result': result}).encode()


class Store:
    def __init__(self):
        self.inbox = {}
        self.completed = set()
        self.outbox = []
        self.acks = []
        self.failures = []
        self.events = []

    def polling_offset(self, bot_id):
        return max(self.inbox, default=9) + 1

    def save_update(self, bot_id, update_id, payload, now):
        self.events.append(('save', update_id))
        self.inbox.setdefault(update_id, {'update_id': update_id, 'payload': payload})

    def pending_updates(self, limit=100):
        return [record for key, record in self.inbox.items() if key not in self.completed][:limit]

    def complete_update(self, update_id, replies, now):
        self.events.append(('complete', update_id))
        self.completed.add(update_id)
        for reply in replies:
            self.outbox.append({'id': len(self.outbox) + 1, 'lease_token': 'lease-1', **reply})

    def pending_outbox(self, now, limit=20):
        return self.outbox[:limit]

    def ack_outbox(self, id, lease_token, now):
        self.acks.append((id, lease_token))
        self.outbox = [item for item in self.outbox if item['id'] != id]

    def fail_outbox(self, id, lease_token, now):
        self.failures.append((id, lease_token))


class Controller:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def handle(self, payload, now):
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError('sensitive handler error')
        return [{'chat_id': 7, 'text': 'Review only', 'keyboard': [[{'text': 'Confirm', 'data': 'review:opaque'}]]}]


@pytest.fixture
async def boundary():
    request, store, controller = Request(), Store(), Controller()
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        yield TelegramTransport(bot, store, controller), request, store, controller


async def test_duplicate_and_durable_before_completion(boundary):
    transport, request, store, controller = boundary
    assert await transport.poll_once(NOW) == 1
    assert len(controller.calls) == 1
    assert store.events == [('save', 10), ('save', 10), ('complete', 10)]
    assert await transport.poll_once(NOW) == 0
    updates = [params for method, params in request.calls if method == 'getUpdates']
    assert [item['offset'] for item in updates] == [10, 11]
    assert not any(method == 'deleteWebhook' for method, _ in request.calls)


async def test_handler_failure_retained_then_recovered(boundary, caplog):
    transport, request, store, controller = boundary
    controller.fail = True
    assert await transport.poll_once(NOW) == 0
    assert store.pending_updates()
    assert not store.outbox
    assert 'sensitive handler error' not in caplog.text
    controller.fail = False
    request.updates = []
    assert await transport.poll_once(NOW) == 1
    assert not store.pending_updates()


async def test_timeout_is_at_least_once_and_fenced(boundary):
    transport, request, store, controller = boundary
    await transport.poll_once(NOW)
    request.timeout_send = True
    assert await transport.deliver_once(NOW) == 0
    assert store.failures == [(1, 'lease-1')]
    assert not store.acks and store.outbox
    request.timeout_send = False
    assert await transport.deliver_once(NOW) == 1
    assert store.acks == [(1, 'lease-1')]
    sends = [params for method, params in request.calls if method == 'sendMessage']
    assert len(sends) == 2
    assert sends[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'review:opaque'


async def test_callback_answer_no_financial_api(boundary):
    transport, request, store, controller = boundary
    request.updates = [{'update_id': 11, 'callback_query': {'id': 'callback-id', 'from': {'id': 7, 'is_bot': False, 'first_name': 'User'}, 'chat_instance': 'abc', 'data': 'confirm:opaque'}}]
    assert await transport.poll_once(NOW) == 1
    assert any(method == 'answerCallbackQuery' for method, _ in request.calls)
    assert controller.calls[0]['callback_query']['data'] == 'confirm:opaque'


def test_factory_disables_redirects():
    bot = create_bot('123:TEST')
    assert isinstance(bot, Bot)
    for request in bot.request, bot._request[0]:
        assert request._client.follow_redirects is False


async def test_keyboard_accepts_real_ptb(boundary):
    transport, request, store, controller = boundary
    store.outbox = [{'id': 1, 'lease_token': 'new-lease', 'chat_id': 7, 'text': 'Hello', 'keyboard': None}]
    assert await transport.deliver_once(NOW) == 1
    assert store.acks == [(1, 'new-lease')]
    assert InlineKeyboardMarkup.de_json({'inline_keyboard': [[{'text': 'OK', 'callback_data': 'ok'}]]}, None)


async def test_save_failure_never_handles_or_advances_offset(boundary, monkeypatch):
    transport, request, store, controller = boundary

    def fail(*args):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(store, 'save_update', fail)
    with pytest.raises(RuntimeError):
        await transport.poll_once(NOW)
    assert not controller.calls and not store.completed
    assert store.polling_offset(99) == 10


async def test_ack_failure_retains_claim_for_replay(boundary, monkeypatch):
    transport, request, store, controller = boundary
    await transport.poll_once(NOW)

    def fail(*args):
        raise RuntimeError('ack lost')

    monkeypatch.setattr(store, 'ack_outbox', fail)
    with pytest.raises(RuntimeError):
        await transport.deliver_once(NOW)
    assert store.outbox and not store.acks
    assert not store.failures


async def test_receive_timeout_still_drains_durable_inbox(boundary, monkeypatch):
    transport, request, store, controller = boundary
    store.save_update(99, 10, PAYLOAD, NOW)

    async def timeout(*args, **kwargs):
        raise TimedOut()

    monkeypatch.setattr(type(transport.bot), 'get_updates', timeout)
    assert await transport.poll_once(NOW) == 1
    assert not store.pending_updates()


async def test_callback_answer_failure_does_not_lose_update(boundary, monkeypatch):
    transport, request, store, controller = boundary
    request.updates = [{'update_id': 11, 'callback_query': {'id': 'callback-id', 'from': {'id': 7, 'is_bot': False, 'first_name': 'User'}, 'chat_instance': 'abc', 'data': 'confirm:opaque'}}]

    async def timeout(*args, **kwargs):
        raise TimedOut()

    monkeypatch.setattr(type(transport.bot), 'answer_callback_query', timeout)
    assert await transport.poll_once(NOW) == 1
    assert not store.pending_updates()


async def test_run_stops_without_bot_startup(boundary):
    import asyncio

    transport, request, store, controller = boundary
    stop = asyncio.Event()
    stop.set()
    await transport.run(stop)
    assert [name for name, _ in request.calls] == ['getMe']


async def test_receipt_timestamp_survives_restart(boundary):
    transport, request, store, controller = boundary
    store.save_update(99, 10, PAYLOAD, NOW)
    store.inbox[10]['received_at'] = NOW.isoformat()
    request.updates = []
    receipts = []

    async def handle(payload, received_at):
        receipts.append(received_at)
        return []

    controller.handle = handle
    restarted = TelegramTransport(transport.bot, store, controller)
    assert await restarted.poll_once(NOW + timedelta(days=1)) == 1
    assert receipts == [NOW]


async def test_json_keyboard_callback_data_list(boundary):
    transport, request, store, _ = boundary
    store.outbox = [{'id': 1, 'lease_token': 'lease', 'chat_id': 7,
                     'text': 'Review', 'keyboard': [[{'text': 'Confirm', 'callback_data': 'rev:opaque'}]]}]
    assert await transport.deliver_once(NOW) == 1
    assert request.calls[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'rev:opaque'


async def test_callback_ack_is_bounded(boundary, monkeypatch):
    transport, request, store, _ = boundary
    request.updates = [{'update_id': 11, 'callback_query': {'id': 'callback-id', 'from': {'id': 7, 'is_bot': False, 'first_name': 'User'}, 'chat_instance': 'abc'}}]

    async def hangs(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(type(transport.bot), 'answer_callback_query', hangs)
    monkeypatch.setattr('budget_bot.telegram.transport.CALLBACK_ACK_TIMEOUT', 0.01, raising=False)
    assert await asyncio.wait_for(transport.poll_once(NOW), 0.5) == 1
    assert not store.pending_updates()


async def test_long_reply_is_durably_split_with_keyboard_on_last(boundary):
    transport, request, store, controller = boundary
    text = '₹😀' * 3000
    keyboard = [[{'text': 'Confirm', 'data': 'rev:opaque'}]]

    async def handle(payload, received_at):
        return [{'chat_id': 7, 'text': text, 'keyboard': keyboard}]

    controller.handle = handle
    assert await transport.poll_once(NOW) == 1
    assert len(store.outbox) > 1
    assert ''.join(item['text'] for item in store.outbox) == text
    assert all(len(item['text'].encode('utf-16-le')) // 2 <= 4096 for item in store.outbox)
    assert all(not item['keyboard'] for item in store.outbox[:-1])
    assert store.outbox[-1]['keyboard'] == keyboard


async def test_rejected_ack_is_not_counted(boundary, monkeypatch):
    transport, _, store, _ = boundary
    await transport.poll_once(NOW)
    monkeypatch.setattr(store, 'ack_outbox', lambda *args: False)
    assert await transport.deliver_once(NOW) == 0


async def test_malformed_inbox_record_retained_without_blocking_next(boundary):
    transport, request, store, _ = boundary
    request.updates = []
    store.inbox[9] = {'update_id': 9, 'payload': None}
    store.save_update(99, 10, PAYLOAD, NOW)
    assert await transport.poll_once(NOW) == 1
    assert [record['update_id'] for record in store.pending_updates()] == [9]


async def test_legacy_long_outbox_sends_all_text_and_last_keyboard(boundary):
    transport, request, store, _ = boundary
    text = 'x' * 8193
    store.outbox = [{'id': 1, 'lease_token': 'lease', 'chat_id': 7,
                     'text': text, 'keyboard': {'inline_keyboard': [[{'text': 'OK', 'callback_data': 'ok'}]]}}]
    assert await transport.deliver_once(NOW) == 1
    sends = [params for method, params in request.calls if method == 'sendMessage']
    assert [len(item['text']) for item in sends] == [4096, 4096, 1]
    assert ''.join(item['text'] for item in sends) == text
    assert all('reply_markup' not in item for item in sends[:-1])
    assert sends[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'ok'


async def test_run_recovers_after_cycle_failure(boundary, monkeypatch):
    transport, _, _, _ = boundary
    stop = asyncio.Event()
    cycles = []

    async def poll(now):
        cycles.append('poll')
        if cycles.count('poll') == 1:
            raise RuntimeError('safe cycle failure')
        stop.set()
        return 0

    async def deliver(now):
        cycles.append('deliver')
        return 0

    monkeypatch.setattr(transport, 'poll_once', poll)
    monkeypatch.setattr(transport, 'deliver_once', deliver)
    await asyncio.wait_for(transport.run(stop), 2)
    assert cycles == ['poll', 'deliver', 'poll', 'deliver']
