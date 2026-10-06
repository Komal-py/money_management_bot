"""Guided Add expense / Add income: buttons collect fields, workflow owns review."""
from datetime import timedelta

import pytest

from budget_bot.controller import BudgetController
from budget_bot.domain.errors import BudgetError
from test_controller import NOW, message


class Store:
    def __init__(self, buckets=('Travel', 'Food')):
        self.buckets = {name: {'id': i, 'balance': 0, 'target': None} for i, name in enumerate(buckets)}

    def get_user(self, telegram_id):
        return {'owner_id': 'owner-1', 'active': True, 'onboarded': True, 'admin': False,
                'timezone': 'Asia/Kolkata'} if telegram_id == 1 else None

    def get_snapshot(self, owner_id):
        return {'buckets': dict(self.buckets)}


class Workflow:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def submit(self, owner, actions, received, request_id=None):
        if self.fail:
            raise BudgetError(self.fail, 'Finish or cancel the current interaction first.')
        self.calls.append((owner, actions, request_id))
        rid = '00000000-0000-4000-8000-000000000001'
        return {'text': 'Review required', 'keyboard': [[
            {'text': d.title(), 'data': f'rev:{rid}:1:{d}'} for d in ('confirm', 'edit', 'cancel')]]}


class Conversation:
    def __init__(self):
        self.calls = []

    async def dispatch(self, *args, **kwargs):
        self.calls.append(args)
        return {'text': 'Conversation boundary', 'keyboard': []}

    def menu(self):
        return {'text': 'Budget menu', 'keyboard': []}


def button(data, update_id=7):
    return {'update_id': update_id, 'callback_query': {
        'id': 'cb', 'from': {'id': 1, 'is_bot': False}, 'data': data,
        'message': {'message_id': 1, 'chat': {'id': 1, 'type': 'private'}}}}


def labels(reply):
    return {item['text']: item['data'] for row in reply['keyboard'] for item in row}


def app(store=None, workflow=None):
    return BudgetController(store or Store(), workflow or Workflow(), None, None, None,
                            conversation=Conversation())


async def say(controller, text, update_id=1):
    payload = message(text=text)
    payload['update_id'] = update_id
    return (await controller.handle(payload, NOW))[0]


@pytest.mark.asyncio
async def test_menu_offers_guided_entry_buttons():
    reply = await say(app(), '/start')
    assert labels(reply)['Add expense'] == 'entry:expense'
    assert labels(reply)['Add income'] == 'entry:income'
    assert all(len(item['data'].encode()) <= 64 for row in reply['keyboard'] for item in row)


@pytest.mark.asyncio
async def test_guided_expense_ends_in_normal_review():
    workflow = Workflow()
    controller = app(workflow=workflow)
    reply = (await controller.handle(button('entry:expense'), NOW))[0]
    assert 'how much' in reply['text'].lower() and 'Back' not in labels(reply)
    reply = await say(controller, '1,250.5', 2)
    assert '₹1250.50' in reply['text'] and labels(reply)['Food'].startswith('entry:bucket:')
    reply = (await controller.handle(button(labels(reply)['Travel'], 3), NOW))[0]
    assert 'description' in reply['text'].lower()
    reply = await say(controller, '  Train tickets ', 4)
    assert labels(reply)['Today'] == 'entry:date:today'
    assert workflow.calls == []
    reply = (await controller.handle(button('entry:date:yesterday', 5), NOW))[0]
    assert reply['text'] == 'Review required'
    assert [b['text'] for b in reply['keyboard'][0]] == ['Confirm', 'Edit', 'Cancel']
    (owner, actions, request_id), = workflow.calls
    assert owner == 'owner-1' and request_id
    assert actions == [{'type': 'expense', 'amount_inr': '1250.50', 'description': 'Train tickets',
                        'date_expression': 'yesterday', 'bucket_name': 'Travel'}]
    assert not controller.guided.active('owner-1', NOW)
    # Afterwards ordinary text goes back to the conversation router.
    assert (await say(controller, 'hello', 6))['text'] == 'Conversation boundary'


@pytest.mark.asyncio
async def test_guided_income_typed_bucketless_with_iso_date():
    workflow = Workflow()
    controller = app(store=Store(buckets=()), workflow=workflow)
    await controller.handle(button('entry:income'), NOW)
    await say(controller, '5000', 2)
    await say(controller, 'Salary', 3)
    reply = await say(controller, '2026-10-01', 4)
    assert reply['text'] == 'Review required'
    assert workflow.calls[0][1] == [{'type': 'income', 'amount_inr': '5000.00', 'description': 'Salary',
                                     'date_expression': '2026-10-01'}]


@pytest.mark.asyncio
async def test_invalid_answers_reprompt_without_submitting():
    workflow = Workflow()
    controller = app(workflow=workflow)
    await controller.handle(button('entry:expense'), NOW)
    for bad in ('0', '-5', '1.001', 'abc', '1e3'):
        assert 'positive INR amount' in (await say(controller, bad))['text']
    await say(controller, '10', 2)
    reply = await say(controller, 'Nope', 3)
    assert reply['text'].startswith('No bucket by that name')
    reply = await say(controller, 'travel', 4)  # case-insensitive, canonical name kept
    await say(controller, 'Bus', 5)
    assert 'YYYY-MM-DD' in (await say(controller, '2026-02-30', 6))['text']
    assert (await controller.handle(button('entry:bucket:0', 7), NOW))[0]['text'].startswith('That button is not current')
    assert workflow.calls == []
    await say(controller, 'TODAY', 8)
    assert workflow.calls[0][1][0]['bucket_name'] == 'Travel'
    assert workflow.calls[0][1][0]['date_expression'] == 'today'


@pytest.mark.asyncio
async def test_back_cancel_and_slash_cancel():
    workflow = Workflow()
    controller = app(workflow=workflow)
    await controller.handle(button('entry:expense'), NOW)
    await say(controller, '10', 2)
    reply = (await controller.handle(button('entry:back', 3), NOW))[0]
    assert 'how much' in reply['text'].lower()
    reply = (await controller.handle(button('entry:cancel', 4), NOW))[0]
    assert 'cancelled' in reply['text'].lower()
    assert (await say(controller, '10', 5))['text'] == 'Conversation boundary'
    stale = (await controller.handle(button('entry:back', 6), NOW))[0]
    assert 'no longer active' in stale['text']
    await controller.handle(button('entry:income', 7), NOW)
    assert 'Nothing was saved' in (await say(controller, '/cancel', 8))['text']
    assert workflow.calls == []


@pytest.mark.asyncio
async def test_expense_without_buckets_and_draft_expiry():
    controller = app(store=Store(buckets=()))
    reply = (await controller.handle(button('entry:expense'), NOW))[0]
    assert '/bucket' in reply['text'] and not controller.guided.active('owner-1', NOW)
    await controller.handle(button('entry:income'), NOW)
    assert controller.guided.active('owner-1', NOW)
    assert not controller.guided.active('owner-1', NOW + timedelta(hours=1))


@pytest.mark.asyncio
async def test_pending_review_keeps_draft_for_retry():
    workflow = Workflow(fail='pending_review')
    controller = app(workflow=workflow)
    await controller.handle(button('entry:income'), NOW)
    await say(controller, '10', 2)
    await say(controller, 'Gift', 3)
    reply = (await controller.handle(button('entry:date:today', 4), NOW))[0]
    assert 'Finish or cancel' in reply['text']
    assert controller.guided.active('owner-1', NOW)
    workflow.fail = None
    reply = (await controller.handle(button('entry:date:today', 5), NOW))[0]
    assert reply['text'] == 'Review required'


@pytest.mark.asyncio
async def test_entry_buttons_require_onboarding_and_reject_garbage():
    class Fresh(Store):
        def get_user(self, telegram_id):
            return {**super().get_user(telegram_id), 'onboarded': False}
    controller = app(store=Fresh())
    assert 'setup' in (await controller.handle(button('entry:expense'), NOW))[0]['text'].lower()
    controller = app()
    await controller.handle(button('entry:expense'), NOW)
    for data in ('entry:bucket:99', 'entry:date:tomorrow', 'entry:x:y:z'):
        assert 'not current' in (await controller.handle(button(data), NOW))[0]['text']
