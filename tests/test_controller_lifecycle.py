"""Real store/setup/graph lifecycle with only the R3 dispatch seam injected."""
from uuid import uuid4

import pytest

from test_controller import NOW
import test_controller_ingress as ingress
from test_controller_ingress import private, button

db = ingress.db
actor = ingress.actor
app = ingress.app


async def setup_review(app, telegram_id):
    for text in ('/start', '100', 'Travel', '40'):
        await app.handle(private(telegram_id, text), NOW)
    return await app.handle(button(telegram_id, 'setup:finish'), NOW)


def review_data(review, decision):
    return f'rev:{review["request_id"]}:{review["revision"]}:{decision}'


@pytest.mark.asyncio
async def test_guided_setup_review_confirm_and_duplicate_are_financially_safe(db, actor, app):
    telegram_id, owner = actor
    replies = await setup_review(app, telegram_id)
    review = db.get_pending(owner)
    assert review is not None
    assert 'Amount: INR 100.00' in replies[0]['text'] and 'Amount: INR 40.00' in replies[0]['text']
    assert 'Asia/Kolkata' in replies[0]['text']
    assert [item['data'] for item in replies[0]['keyboard'][0]] == [
        review_data(review, decision) for decision in ('confirm', 'edit', 'cancel')]
    assert db.get_snapshot(owner)['transactions'] == []
    for _ in range(2):
        response = await app.handle(button(telegram_id, review_data(review, 'confirm')), NOW)
        assert 'recorded' in response[0]['text'].lower()
    snapshot = db.get_snapshot(owner)
    assert snapshot['onboarded'] and snapshot['pool'] == 6000
    assert snapshot['buckets']['Travel']['balance'] == 4000
    assert len(snapshot['transactions']) == 2
    response = await app.handle(private(telegram_id, '/start'), NOW)
    assert response[0]['text'] == 'Budget menu'


@pytest.mark.asyncio
async def test_setup_buttons_answers_and_safe_invalid_input_precede_conversation(db, actor, app):
    telegram_id, owner = actor
    response = await app.handle(private(telegram_id, '/start'), NOW)
    assert 'opening' in response[0]['text'].lower()
    response = await app.handle(private(telegram_id, 'not an amount'), NOW)
    assert 'amount' in response[0]['text'].lower() or 'money' in response[0]['text'].lower()
    await app.handle(private(telegram_id, '0'), NOW)
    response = await app.handle(button(telegram_id, 'setup:back'), NOW)
    assert 'opening' in response[0]['text'].lower()
    assert app.conversation.calls == []
    assert db.get_snapshot(owner)['transactions'] == []


@pytest.mark.asyncio
async def test_setup_edit_exact_review_restarts_but_old_or_foreign_edit_cannot_reset(db, actor, app):
    telegram_id, owner = actor
    await setup_review(app, telegram_id)
    old = db.get_pending(owner)
    response = await app.handle(button(telegram_id, review_data(old, 'edit')), NOW)
    assert 'opening' in response[0]['text'].lower()
    assert db.get_pending(owner) is None
    for text in ('200', 'Food', '50'):
        await app.handle(private(telegram_id, text), NOW)
    await app.handle(button(telegram_id, 'setup:finish'), NOW)
    current = db.get_pending(owner)
    assert current['request_id'] != old['request_id']
    stranger_id = uuid4().int % (2**52 - 1) + 1
    stranger = db.ensure_admin(stranger_id)
    await app.handle(private(stranger_id, '/start'), NOW)
    for identity, data in ((telegram_id, review_data(old, 'edit')),
                           (telegram_id, review_data({**current, 'revision': 2}, 'edit')),
                           (stranger_id, review_data(current, 'edit'))):
        response = await app.handle(button(identity, data), NOW)
        assert 'review' in response[0]['text'].lower()
        assert '₹' not in response[0]['text']
    assert db.get_pending(owner) == current
    response = await app.handle(private(stranger_id, '/start'), NOW)
    assert 'opening' in response[0]['text'].lower()
    assert db.get_snapshot(stranger)['transactions'] == []


@pytest.mark.asyncio
async def test_old_cancel_replay_cannot_cancel_new_setup_review(db, actor, app):
    telegram_id, owner = actor
    await setup_review(app, telegram_id)
    old = db.get_pending(owner)
    await app.handle(button(telegram_id, review_data(old, 'cancel')), NOW)
    await setup_review(app, telegram_id)
    current = db.get_pending(owner)
    assert current is not None and current['request_id'] != old['request_id']
    await app.handle(button(telegram_id, review_data(old, 'cancel')), NOW)
    assert db.get_pending(owner) == current
    assert app.onboarding.active(owner)


@pytest.mark.asyncio
async def test_setup_review_revision_refresh_then_stale_edit_does_not_reset(db, actor, app):
    telegram_id, owner = actor
    await setup_review(app, telegram_id)
    old = db.get_pending(owner)
    db.set_timezone(owner, 'UTC')
    response = await app.handle(button(telegram_id, review_data(old, 'confirm')), NOW)
    current = db.get_pending(owner)
    assert current['revision'] == old['revision'] + 1
    assert 'confirm' in response[0]['keyboard'][0][0]['data']
    await app.handle(button(telegram_id, review_data(old, 'edit')), NOW)
    assert db.get_pending(owner) == current
    assert db.get_snapshot(owner)['transactions'] == []
    response = await app.handle(button(telegram_id, review_data(current, 'edit')), NOW)
    assert 'opening' in response[0]['text'].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('via_button', [False, True])
async def test_setup_cancel_leaves_no_finances_and_start_restarts(db, actor, app, via_button):
    telegram_id, owner = actor
    await setup_review(app, telegram_id)
    review = db.get_pending(owner)
    payload = button(telegram_id, review_data(review, 'cancel')) if via_button else private(telegram_id, '/cancel')
    response = await app.handle(payload, NOW)
    assert 'cancel' in response[0]['text'].lower()
    assert db.get_pending(owner) is None
    assert not app.onboarding.active(owner)
    assert db.get_snapshot(owner)['transactions'] == []
    response = await app.handle(private(telegram_id, '/start'), NOW)
    assert 'opening' in response[0]['text'].lower()


@pytest.mark.asyncio
async def test_admin_commands_are_access_only_and_members_denied(db, actor, app):
    telegram_id, admin = actor
    response = await app.handle(private(telegram_id, '/invite'), NOW)
    assert 'Single-use invite' in response[0]['text']
    member_id = uuid4().int % (2**52 - 1) + 1
    member = db.redeem_invite(db.create_invite(admin, NOW), member_id, NOW)
    for command in ('/users', '/invite', f'/revoke {telegram_id}'):
        response = await app.handle(private(member_id, command), NOW)
        assert 'administrator' in response[0]['text'].lower()
        assert str(telegram_id) not in response[0]['text']
        assert '₹' not in response[0]['text']
    response = await app.handle(private(telegram_id, '/users'), NOW)
    assert str(member_id) in response[0]['text'] and '₹' not in response[0]['text']
    response = await app.handle(private(telegram_id, f'/revoke {member_id}'), NOW)
    assert 'revoked' in response[0]['text'].lower()
    assert db.get_user(member_id)['active'] is False
    assert db.get_snapshot(admin)['transactions'] == []
    assert member != admin


@pytest.mark.asyncio
async def test_timezone_and_cancel_pending_financial_commands(db, actor, app):
    telegram_id, owner = actor
    review = db.propose(owner, [{'type': 'opening', 'amount_inr': '100'}], NOW)
    db.confirm(owner, review['request_id'], review['revision'], NOW)
    response = await app.handle(private(telegram_id, '/timezone UTC'), NOW)
    assert 'UTC' in response[0]['text']
    assert db.get_user(telegram_id)['timezone'] == 'UTC'
    response = await app.handle(private(telegram_id, '/timezone invalid/zone'), NOW)
    assert 'timezone' in response[0]['text'].lower()
    await app.handle(private(telegram_id, '/income 10 Salary'), NOW)
    assert db.get_pending(owner) is not None
    assert db.get_snapshot(owner)['pool'] == 10000
    response = await app.handle(private(telegram_id, '/cancel'), NOW)
    assert 'cancel' in response[0]['text'].lower()
    assert db.get_pending(owner) is None and db.get_snapshot(owner)['pool'] == 10000
    response = await app.handle(private(telegram_id, '/cancel'), NOW)
    assert 'nothing' in response[0]['text'].lower() or 'no pending' in response[0]['text'].lower()


@pytest.mark.asyncio
async def test_nl_query_calendar_use_only_frozen_conversation_seam(db, actor, app):
    telegram_id, owner = actor
    review = db.propose(owner, [{'type': 'opening', 'amount_inr': '0'}], NOW)
    db.confirm(owner, review['request_id'], review['revision'], NOW)
    for payload in (private(telegram_id, 'spending this week'), private(telegram_id, '/balance'),
                    private(telegram_id, '/calendar'), button(telegram_id, 'cal:2026-10'),
                    button(telegram_id, 'day:2026-10-06')):
        response = await app.handle(payload, NOW)
        assert response[0]['text'] == 'Conversation boundary'
    calls = app.conversation.calls
    assert len(calls) == 5 and all(call[0] == owner for call in calls)
    assert calls[1][4]['report'] == 'balances' and calls[2][4]['report'] == 'calendar'
    assert calls[3][5] == 'cal:2026-10' and calls[4][5] == 'day:2026-10-06'


@pytest.mark.asyncio
async def test_database_failure_is_not_a_successful_reply(db, actor, app, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def broken(*args, **kwargs):
        raise OperationalError('synthetic statement', {}, RuntimeError('synthetic outage'))

    monkeypatch.setattr(db, 'set_timezone', broken)
    with pytest.raises(OperationalError):
        await app.handle(private(actor[0], '/timezone UTC'), NOW)


@pytest.mark.asyncio
async def test_cancel_current_workflow_question_without_pending_review(db, actor, app):
    from budget_bot.domain.errors import BudgetError

    telegram_id, owner = actor
    review = db.propose(owner, [{'type': 'opening', 'amount_inr': '0'}], NOW)
    db.confirm(owner, review['request_id'], review['revision'], NOW)
    output = await app.workflow.submit(owner, [{'type': 'income', 'amount_inr': '10'}], NOW)
    assert output['review'] is None and 'description' in output['text']
    response = await app.handle(private(telegram_id, '/cancel'), NOW)
    assert 'cancel' in response[0]['text'].lower()
    with pytest.raises(BudgetError, match='no current question'):
        await app.workflow.answer(owner, 'Gift', NOW)
    assert db.get_snapshot(owner)['pool'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['confirm', 'edit', 'cancel'])
async def test_foreign_financial_review_never_discloses_or_mutates(db, actor, app, decision):
    telegram_id, owner = actor
    review = db.propose(owner, [{'type': 'opening', 'amount_inr': '0'}], NOW)
    db.confirm(owner, review['request_id'], review['revision'], NOW)
    foreign_id = uuid4().int % (2**52 - 1) + 1
    foreign = db.ensure_admin(foreign_id)
    proposal = db.propose(foreign, [{'type': 'opening', 'amount_inr': '123.45'}], NOW)
    before = db.get_pending(foreign)
    response = await app.handle(button(telegram_id, review_data(proposal, decision)), NOW)
    assert 'not found' in response[0]['text'].lower()
    assert '123.45' not in response[0]['text'] and response[0]['keyboard'] == []
    assert db.get_pending(foreign) == before
    assert db.get_snapshot(foreign)['transactions'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['get_user', 'redeem_invite', 'propose', 'confirm'])
async def test_database_failure_boundaries_remain_retryable(db, actor, app, monkeypatch, boundary):
    from sqlalchemy.exc import OperationalError

    telegram_id, owner = actor
    if boundary == 'redeem_invite':
        telegram_id = uuid4().int % (2**52 - 1) + 1
        payload = private(telegram_id, f'/start {db.create_invite(owner, NOW)}')
    elif boundary in {'propose', 'confirm'}:
        for text in ('/start', '100', 'Travel', '40'):
            await app.handle(private(telegram_id, text), NOW)
        payload = button(telegram_id, 'setup:finish')
        if boundary == 'confirm':
            await app.handle(payload, NOW)
            payload = button(telegram_id, review_data(db.get_pending(owner), 'confirm'))
    else:
        payload = private(telegram_id, '/help')

    def broken(*args, **kwargs):
        raise OperationalError('synthetic statement', {}, RuntimeError('synthetic outage'))

    monkeypatch.setattr(db, boundary, broken)
    with pytest.raises(OperationalError):
        await app.handle(payload, NOW)
