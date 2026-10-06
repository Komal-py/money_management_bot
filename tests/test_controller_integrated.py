"""Assembled private ingress, actual R3 router, PostgreSQL graph and SDK wire.

Controlled HTTP only; no bot startup, live API, owner balances or schema truncation.
"""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from budget_bot.ai import render_result, render_review
from budget_bot.controller import BudgetController
from budget_bot.services.access import AccessService
from budget_bot.services.onboarding import OnboardingService
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.workflows import BudgetWorkflow, postgres_checkpointer
from test_ai_provider import client_for, mutation, response

NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)


@pytest.fixture
def db():
    assert os.environ['BUDGET_TEST_SCHEMA'] == 'test_w6'
    store = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w6')
    store.initialize()
    yield store
    store.close()


def identity():
    return uuid4().int % (2**50) + 1


def private(actor, text, at=NOW):
    return {'update_id': identity(), 'message': {
        'from': {'id': actor, 'is_bot': False}, 'chat': {'id': actor, 'type': 'private'},
        'text': text, 'date': int(at.timestamp())}}


def button(actor, data):
    return {'callback_query': {'id': str(uuid4()), 'from': {'id': actor, 'is_bot': False},
        'message': {'chat': {'id': actor, 'type': 'private'}}, 'data': data}}


def control(reply, label):
    return next(item['data'] for row in reply[0]['keyboard'] for item in row if item['text'] == label)


def assembled(store, saver, interpreter=None):
    onboarding = OnboardingService(store)
    onboarding.initialize()
    flow = BudgetWorkflow(store, interpreter, saver)
    flow.render_review = render_review
    flow.render_result = render_result
    return BudgetController(store, flow, onboarding, ReportService(store), AccessService(store))


async def guided_setup(app, actor):
    for text in ('/start', '100', 'Travel', '40'):
        reply = await app.handle(private(actor, text), NOW)
    return await app.handle(button(actor, control(reply, 'Review')), NOW)


async def confirm(app, actor, reply, now=NOW):
    return await app.handle(button(actor, control(reply, 'Confirm')), now)


async def test_actual_invite_setup_menu_commands_calendar_and_offline_nl(db):
    admin_id, actor = identity(), identity()
    admin = db.ensure_admin(admin_id)
    invite = db.create_invite(admin, NOW)
    async with postgres_checkpointer(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w6_workflow') as saver:
        app = assembled(db, saver)
        reply = await app.handle(private(actor, '/start ' + invite), NOW)
        assert 'opening' in reply[0]['text'].lower()
        owner = db.get_user(actor)['owner_id']
        initial = db.get_snapshot(owner)
        review = await guided_setup(app, actor)
        assert db.get_snapshot(owner) == initial
        saved = await confirm(app, actor, review)
        assert 'recorded' in saved[0]['text'].lower()
        menu = await app.handle(private(actor, '/start'), NOW)
        assert 'virtual INR' in menu[0]['text'] and '/calendar' in menu[0]['text']
        assert app.conversation.__class__.__name__ == 'ConversationRouter'
        before = db.get_snapshot(owner)
        assert before['onboarded'] and before['pool'] == 6000
        assert '₹60.00' in (await app.handle(private(actor, '/balance'), NOW))[0]['text']
        assert '₹0.00' in (await app.handle(private(actor, '/spending today'), NOW))[0]['text']
        calendar = await app.handle(private(actor, '/calendar'), NOW)
        assert calendar[0]['text'] == 'October 2026'
        day = await app.handle(button(actor, 'day:2026-10-06:0'), NOW)
        assert 'No active expenses in this period.' in day[0]['text']
        offline = await app.handle(private(actor, 'Record Metro expense 10 in Travel'), NOW)
        assert 'unavailable' in offline[0]['text'].lower() and db.get_snapshot(owner) == before
        command_review = await app.handle(private(actor, '/expense 10 Travel Metro'), NOW)
        assert db.get_snapshot(owner) == before and control(command_review, 'Confirm').startswith('rev:')
        await app.handle(private(actor, '/cancel'), NOW)
        assert db.get_snapshot(owner) == before and db.get_pending(owner) is None


async def test_actual_nl_expense_reopen_foreign_confirm_replay_and_owner_scoped_correction(db, monkeypatch):
    actor, foreign_actor = identity(), identity()
    owner, foreign_owner = db.ensure_admin(actor), db.ensure_admin(foreign_actor)
    outputs = iter([mutation({'type': 'expense', 'amount_inr': '10', 'bucket_name': 'Travel',
                              'description': 'Metro', 'date_expression': 'today'}),
                    mutation({'type': 'correct', 'reference': 'Metro', 'changes': {'amount_inr': '7'}})])
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(next(outputs))))
    try:
        url = os.environ['BUDGET_TEST_DATABASE_URL']
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            app = assembled(db, saver, client)
            await confirm(app, actor, await guided_setup(app, actor))
            baseline = db.get_snapshot(owner)
            review = await app.handle(private(actor, 'Record Metro expense 10 in Travel'), NOW)
            assert 'Amount: INR 10.00' in review[0]['text'] and db.get_snapshot(owner) == baseline
            callback = control(review, 'Confirm')
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            app = assembled(db, saver, client)
            foreign_before = db.get_snapshot(foreign_owner)
            foreign = await app.handle(button(foreign_actor, callback), NOW)
            assert 'INR 10.00' not in foreign[0]['text'] and db.get_snapshot(owner) == baseline
            assert db.get_snapshot(foreign_owner) == foreign_before
            done = await app.handle(button(actor, callback), NOW)
            assert 'recorded' in done[0]['text'].lower()
            saved = db.get_snapshot(owner)
            assert saved['buckets']['Travel']['balance'] == 3000
            assert await app.handle(button(actor, callback), NOW) == done
            assert db.get_snapshot(owner) == saved
            correction = await app.handle(private(actor, 'Correct the Metro expense to 7'), NOW)
            tx = next(item for item in saved['transactions'] if item['type'] == 'expense')
            assert tx['id'] in correction[0]['text'] and not correction[0]['keyboard']
            assert db.get_snapshot(owner) == saved
            review = await app.handle(private(actor, tx['id']), NOW)
            assert control(review, 'Confirm').startswith('rev:') and db.get_snapshot(owner) == saved
            await confirm(app, actor, review)
            assert db.get_snapshot(owner)['buckets']['Travel']['balance'] == 3300
            report = await app.handle(private(actor, '/spending today'), NOW)
            assert '₹7.00' in report[0]['text'] and db.spending(owner, NOW.date(), NOW.date())['count'] == 1
    finally:
        await client.close()


async def test_actual_controller_clarification_reopen_uses_original_month(db, monkeypatch):
    received = datetime(2026, 10, 31, 18, 29, tzinfo=timezone.utc)
    answered = received + timedelta(minutes=2)
    actor = identity()
    owner = db.ensure_admin(actor)
    outputs = iter([
        {'schema_version': 1, 'kind': 'clarification', 'actions': [], 'missing_fields': ['period'],
         'clarification_question': 'Which reporting period?', 'query': None},
        {'schema_version': 1, 'kind': 'query', 'actions': [], 'missing_fields': [],
         'clarification_question': None, 'query': {'report': 'calendar', 'period': 'month',
                                                 'start': None, 'end': None, 'bucket_name': None}}])
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(next(outputs))))
    try:
        url = os.environ['BUDGET_TEST_DATABASE_URL']
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            app = assembled(db, saver, client)
            await confirm(app, actor, await guided_setup(app, actor))
            baseline = db.get_snapshot(owner)
            question = await app.handle(private(actor, 'Show my expense calendar', received), received)
            assert 'reporting period' in question[0]['text'] and db.get_snapshot(owner) == baseline
        async with postgres_checkpointer(url, schema='test_w6_workflow') as saver:
            app = assembled(db, saver, client)
            completed = await app.handle(private(actor, 'this month', answered), answered)
            assert completed[0]['text'] == 'October 2026' and completed[0]['keyboard']
            assert db.get_pending(owner) is None and db.get_snapshot(owner) == baseline
    finally:
        await client.close()
