from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.calls = []

    def get_user(self, telegram_id):
        self.calls.append(telegram_id)
        return {'owner_id': 'owner-1', 'active': True, 'onboarded': True, 'admin': False,
                'timezone': 'Asia/Kolkata'} if telegram_id == 1 else None


def message(sender=1, chat=1, kind='private', text='/help'):
    return {'update_id': 1, 'message': {'message_id': 1, 'date': int(NOW.timestamp()),
            'from': {'id': sender, 'is_bot': False, 'first_name': 'Test'},
            'chat': {'id': chat, 'type': kind}, 'text': text}}


@pytest.mark.asyncio
async def test_groups_rejected_before_store_lookup():
    from budget_bot.controller import BudgetController
    store = Store()
    controller = BudgetController(store, None, None, None, None)
    replies = await controller.handle(message(chat=-1, kind='group'), NOW)
    assert store.calls == []
    assert replies == []


@pytest.mark.asyncio
async def test_sender_mismatch_rejected_before_store_lookup():
    from budget_bot.controller import BudgetController
    store = Store()
    controller = BudgetController(store, None, None, None, None)
    assert await controller.handle(message(chat=2), NOW) == []
    assert store.calls == []


@pytest.mark.asyncio
async def test_uninvited_user_gets_no_financial_access():
    from budget_bot.controller import BudgetController
    store = Store()
    controller = BudgetController(store, None, None, None, None)
    replies = await controller.handle(message(sender=2, chat=2), NOW)
    assert 'invite' in replies[0]['text'].lower()


class Workflow:
    def __init__(self):
        self.calls = []

    async def submit(self, owner, actions, received):
        self.calls.append(('submit', owner, actions))
        return {'text': 'Review required', 'keyboard': []}

    async def decide(self, owner, request, revision, decision, now):
        self.calls.append(('decide', owner, request, revision, decision))
        return {'text': 'Saved once', 'keyboard': []}


@pytest.mark.asyncio
async def test_financial_command_only_submits_review():
    from budget_bot.controller import BudgetController
    workflow = Workflow()
    def parse(*_):
        return {'kind': 'mutation', 'actions': [{'type': 'income', 'amount_inr': '100'}]}
    controller = BudgetController(Store(), workflow, None, None, None, command_parser=parse)
    replies = await controller.handle(message(text='/income 100 Salary'), NOW)
    assert replies[0]['text'] == 'Review required'
    assert workflow.calls == [('submit', 'owner-1', [{'type': 'income', 'amount_inr': '100'}])]


@pytest.mark.asyncio
async def test_confirmation_callback_bound_to_actor_and_revision():
    from budget_bot.controller import BudgetController
    workflow = Workflow()
    controller = BudgetController(Store(), workflow, None, None, None)
    request = '11111111-1111-4111-8111-111111111111'
    callback = {'update_id': 2, 'callback_query': {'id': 'c1', 'from': {'id': 1, 'is_bot': False},
                'message': {'chat': {'id': 1, 'type': 'private'}},
                'data': f'rev:{request}:2:confirm'}}
    replies = await controller.handle(callback, NOW)
    assert replies[0]['text'] == 'Saved once'
    assert workflow.calls == [('decide', 'owner-1', request, 2, 'confirm')]


@pytest.mark.asyncio
async def test_malformed_confirmation_is_not_executed():
    from budget_bot.controller import BudgetController
    workflow = Workflow()
    controller = BudgetController(Store(), workflow, None, None, None)
    callback = {'callback_query': {'from': {'id': 1, 'is_bot': False},
                'message': {'chat': {'id': 1, 'type': 'private'}}, 'data': 'rev:not-an-id:2:confirm'}}
    await controller.handle(callback, NOW)
    assert workflow.calls == []
