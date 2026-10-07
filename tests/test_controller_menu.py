"""UI-A controller seams: menu buttons become existing commands; period buttons are read-only."""
import pytest

from budget_bot.controller import BudgetController
from budget_bot.telegram import menu
from test_controller import NOW, Store, Workflow, message


class Conversation:
    def __init__(self):
        self.calls = []

    async def dispatch(self, owner_id, text, received_at, now, *, query=None, callback_data=None):
        self.calls.append({'text': text, 'query': query, 'callback_data': callback_data,
                           'received_at': received_at})
        return {'text': 'Report', 'keyboard': [], 'query': query}

    def menu(self):
        return {'text': 'Budget menu', 'keyboard': menu.main_menu_keyboard()}


def press(data, sender=1):
    return {'update_id': 2, 'callback_query': {'id': 'c1', 'from': {'id': sender, 'is_bot': False},
            'message': {'chat': {'id': sender, 'type': 'private'}}, 'data': data}}


def app():
    conversation, workflow = Conversation(), Workflow()
    return BudgetController(Store(), workflow, None, None, None, conversation=conversation), conversation, workflow


def labels(reply):
    return [button['text'] for row in reply['keyboard'] for button in row]


@pytest.mark.parametrize('label,report', [('💰 Balance', 'balances'), ('📅 Calendar', 'calendar')])
async def test_menu_button_runs_the_same_query_as_its_command(label, report):
    controller, conversation, workflow = app()
    await controller.handle(message(text=label), NOW)
    await controller.handle(message(text=menu.button_command(label)), NOW)
    assert conversation.calls[0]['query']['report'] == report
    assert conversation.calls[0] == conversation.calls[1]
    assert workflow.calls == []


async def test_menu_and_help_buttons_reply_with_persistent_keyboard():
    controller, _, _ = app()
    reply = (await controller.handle(message(text='/start'), NOW))[0]
    assert reply['text'] == 'Budget menu' and reply['keyboard'] == menu.main_menu_keyboard()
    reply = (await controller.handle(message(text='❓ Help'), NOW))[0]
    assert '/expense' in reply['text'] and reply['keyboard'] == menu.main_menu_keyboard()


async def test_cancel_button_is_the_cancel_command():
    class Pending(Store):
        def get_pending(self, owner):
            return {'request_id': 'r1', 'revision': 3}
    workflow = Workflow()
    controller = BudgetController(Pending(), workflow, None, None, None, conversation=Conversation())
    await controller.handle(message(text='/cancel'), NOW)
    assert workflow.calls == [('decide', 'owner-1', 'r1', 3, 'cancel')]


def test_main_menu_is_the_six_requested_buttons():
    assert [[b['text'] for b in row] for row in menu.main_menu_keyboard()['keyboard']] == [
        ['💰 Balance', '📊 Spending'], ['➕ Add expense', '💵 Add income'], ['📅 Calendar', '❓ Help']]
    assert menu.main_menu_keyboard()['is_persistent'] is True
    assert menu.button_command('➕ Add expense') == 'entry:expense'
    assert menu.button_command('💵 Add income') == 'entry:income'


@pytest.mark.parametrize('text', ['Balance', '💰 balance', '💰 Balance please', 'spending'])
async def test_similar_free_text_is_not_a_button(text):
    controller, conversation, _ = app()
    await controller.handle(message(text=text), NOW)
    assert conversation.calls[0]['query'] is None and conversation.calls[0]['text'] == text


@pytest.mark.parametrize('text', ['📊 Spending', '/spending', '/spending@BudgetBot', '/spending  '])
async def test_bare_spending_offers_period_buttons_without_reporting(text):
    controller, conversation, workflow = app()
    reply = (await controller.handle(message(text=text), NOW))[0]
    assert labels(reply) == ['Today', 'This week', 'This month']
    assert [b['data'] for row in reply['keyboard'] for b in row] == ['sp:today', 'sp:week', 'sp:month']
    assert conversation.calls == [] and workflow.calls == []


@pytest.mark.parametrize('period', ['today', 'week', 'month'])
async def test_period_button_runs_read_only_spending_query(period):
    controller, conversation, workflow = app()
    reply = (await controller.handle(press(f'sp:{period}'), NOW))[0]
    assert conversation.calls == [{'text': '', 'callback_data': None, 'received_at': NOW, 'query': {
        'report': 'spending', 'period': period, 'start': None, 'end': None, 'bucket_name': None}}]
    assert workflow.calls == []
    assert labels(reply) == ['Today', 'This week', 'This month']


async def test_spending_with_arguments_still_uses_parser():
    controller, conversation, _ = app()
    await controller.handle(message(text='/spending week Travel'), NOW)
    assert conversation.calls[0]['query']['period'] == 'week'
    assert conversation.calls[0]['query']['bucket_name'] == 'Travel'


@pytest.mark.parametrize('data', ['sp:year', 'sp:range', 'sp:', 'sp:today:extra', 'sp:TODAY', 'sp:today '])
async def test_invalid_period_buttons_are_refused(data):
    controller, conversation, _ = app()
    reply = (await controller.handle(press(data), NOW))[0]
    assert conversation.calls == [] and 'not available' in reply['text']


async def test_period_buttons_require_completed_setup():
    class Fresh(Store):
        def get_user(self, telegram_id):
            return {**super().get_user(telegram_id), 'onboarded': False}
    conversation = Conversation()
    controller = BudgetController(Fresh(), Workflow(), None, None, None, conversation=conversation)
    reply = (await controller.handle(press('sp:today'), NOW))[0]
    assert '/start' in reply['text'] and conversation.calls == []


async def test_uninvited_menu_button_gets_no_access():
    controller, conversation, _ = app()
    reply = (await controller.handle(message(sender=2, chat=2, text='💰 Balance'), NOW))[0]
    assert 'invite' in reply['text'].lower() and conversation.calls == []
