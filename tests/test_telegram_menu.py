"""UI-A: command registration, persistent reply keyboard, button mapping, period buttons."""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from telegram import Bot, ReplyKeyboardMarkup
from telegram.request import BaseRequest

from budget_bot.telegram import menu
from budget_bot.telegram.commands import HELP_TEXT
from budget_bot.telegram.transport import TelegramTransport

NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)


def test_registered_commands_are_parseable_and_bounded():
    commands = menu.registered_commands()
    assert commands, 'Registration must offer at least the core commands'
    names = [name for name, _ in commands]
    assert len(names) == len(set(names))
    for name, description in commands:
        assert name == name.lower() and name.isalpha() and 1 <= len(name) <= 32
        assert 1 <= len(description) <= 256
        assert '/' + name in HELP_TEXT


class Request(BaseRequest):
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

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
        elif name == self.fail:
            return 400, json.dumps({'ok': False, 'description': 'SYNTHETIC_SECRET failure'}).encode()
        elif name == 'sendMessage':
            result = {'message_id': 2, 'date': 1791244800, 'chat': {'id': 7, 'type': 'private'},
                      'text': parameters['text']}
        else:
            result = True
        return 200, json.dumps({'ok': True, 'result': result}).encode()


@pytest.fixture
async def sdk():
    request = Request()
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        yield bot, request


async def test_register_commands_scopes_members_and_admin(sdk):
    bot, request = sdk
    assert await TelegramTransport(bot, None, None).register_commands(admin_id=4242) is True
    calls = [params for name, params in request.calls if name == 'setMyCommands']
    assert len(calls) == 2
    member, admin = calls
    assert member['scope'] == {'type': 'all_private_chats'}
    assert [c['command'] for c in member['commands']] == [n for n, _ in menu.registered_commands()]
    assert admin['scope'] == {'type': 'chat', 'chat_id': 4242}
    assert [c['command'] for c in admin['commands']] == [n for n, _ in menu.admin_commands()]
    assert {'invite', 'users', 'revoke'}.isdisjoint(c['command'] for c in member['commands'])


async def test_register_commands_failure_is_logged_without_details(caplog):
    request = Request(fail='setMyCommands')
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        assert await TelegramTransport(bot, None, None).register_commands(admin_id=4242) is False
    assert 'SYNTHETIC_SECRET' not in caplog.text


class Idle:
    """Store double for one transport cycle with nothing durable to do."""

    def __init__(self, stop):
        self.stop = stop

    def polling_offset(self, bot_id):
        self.stop.set()
        return 1

    def save_update(self, *args):
        raise AssertionError('No update was fetched')

    def pending_updates(self, limit=100, *, bot_id=None):
        return []

    def pending_outbox(self, now, limit=20, *, bot_id=None):
        return []


async def test_run_registers_commands_once_before_polling(sdk):
    bot, request = sdk
    stop = asyncio.Event()
    transport = TelegramTransport(bot, Idle(stop), None, admin_id=4242)
    await transport.run(stop)
    names = [name for name, _ in request.calls]
    assert names.count('setMyCommands') == 2
    assert names.index('setMyCommands') < names.index('getUpdates')
    stop.clear()
    await transport.run(stop)
    assert [name for name, _ in request.calls].count('setMyCommands') == 2


async def test_registration_failure_does_not_stop_polling():
    request = Request(fail='setMyCommands')
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        stop = asyncio.Event()
        await TelegramTransport(bot, Idle(stop), None, admin_id=4242).run(stop)
    assert any(name == 'getUpdates' for name, _ in request.calls)


def test_main_menu_is_persistent_six_button_reply_keyboard():
    keyboard = menu.main_menu_keyboard()
    json.dumps(keyboard)
    labels = [button['text'] for row in keyboard['keyboard'] for button in row]
    assert len(labels) == 6 and len(set(labels)) == 6
    assert all(len(row) == 2 for row in keyboard['keyboard'])
    assert keyboard['is_persistent'] is True and keyboard['resize_keyboard'] is True
    assert 'inline_keyboard' not in keyboard
    markup = ReplyKeyboardMarkup.de_json(keyboard, None)
    assert markup.is_persistent and len(markup.keyboard) == 3


async def test_reply_keyboard_survives_durable_split_and_delivery(sdk):
    from budget_bot.telegram.transport import _durable_replies
    bot, request = sdk
    replies = _durable_replies([{'chat_id': 7, 'text': 'x' * 5000, 'keyboard': menu.main_menu_keyboard()}])
    assert replies[0]['keyboard'] is None and replies[-1]['keyboard'] == menu.main_menu_keyboard()

    class Outbox:
        def __init__(self):
            self.items = [{'id': 1, 'lease_token': 'lease', **reply} for reply in replies]
            self.acks = []

        def pending_outbox(self, now, limit=20, *, bot_id=None):
            return [item for item in self.items if item['id'] not in self.acks][:limit]

        def ack_outbox(self, id, lease_token, now):
            self.acks.append(id)

        def fail_outbox(self, *args):
            raise AssertionError('Reply keyboard must be accepted')

    store = Outbox()
    for index, item in enumerate(store.items):
        item['id'] = index + 1
    assert await TelegramTransport(bot, store, None).deliver_once(NOW) == len(replies)
    sends = [params for name, params in request.calls if name == 'sendMessage']
    assert 'reply_markup' not in sends[0]
    assert sends[-1]['reply_markup']['is_persistent'] is True
    assert len(sends[-1]['reply_markup']['keyboard']) == 3
