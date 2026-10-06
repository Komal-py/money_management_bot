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
