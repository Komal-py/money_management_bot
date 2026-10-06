"""Draft-bound setup callback regression and atomic service validation."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, current_thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from budget_bot.domain.errors import BudgetError

import test_controller_ingress as ingress
from test_controller import NOW
from test_controller_ingress import private, button, keyboard_data
from test_controller_lifecycle import setup_review

db = ingress.db
actor = ingress.actor
app = ingress.app


async def test_old_setup_cancel_button_cannot_cancel_newer_setup_review(db, actor, app):
    telegram_id, owner = actor
    reply = (await app.handle(private(telegram_id, '/start'), NOW))[0]
    old_cancel = next(item['data'] for row in reply['keyboard'] for item in row
                      if item['text'] == 'Cancel')
    await app.handle(private(telegram_id, '/cancel'), NOW)
    await setup_review(app, telegram_id)
    pending = db.get_pending(owner)
    assert pending is not None
    with db.engine.connect() as connection:
        before = copy.deepcopy(connection.execute(select(app.onboarding.states.c.state).where(
            app.onboarding.states.c.owner_id == owner)).scalar_one())
    await app.handle(button(telegram_id, old_cancel), NOW)
    assert db.get_pending(owner) == pending
    with db.engine.connect() as connection:
        after = connection.execute(select(app.onboarding.states.c.state).where(
            app.onboarding.states.c.owner_id == owner)).scalar_one()
    assert after == before
    assert app.onboarding.active(owner)
    assert db.get_snapshot(owner)['transactions'] == []


def draft_state(db, app, owner):
    with db.engine.connect() as connection:
        return copy.deepcopy(connection.execute(select(app.onboarding.states.c.state).where(
            app.onboarding.states.c.owner_id == owner)).scalar_one_or_none())


@pytest.mark.parametrize('draft', ['absent', 'replaced', 'cancelled'])
def test_public_expected_request_rejects_noncurrent_draft_without_writes(db, actor, app, draft):
    _, owner = actor
    if draft == 'absent':
        request = '11111111-1111-4111-8111-111111111111'
    else:
        app.onboarding.handle(owner, '/start', NOW)
        request = draft_state(db, app, owner)['request_id']
        app.onboarding.cancel(owner, NOW)
        if draft == 'replaced':
            app.onboarding.handle(owner, '/start', NOW)
        else:
            request = draft_state(db, app, owner)['request_id']
    before = draft_state(db, app, owner)
    with pytest.raises(BudgetError) as rejected:
        app.onboarding.handle(owner, 'setup:cancel', NOW, expected_request=request)
    assert rejected.value.code == 'stale_setup'
    assert draft_state(db, app, owner) == before
    assert db.get_pending(owner) is None
    assert db.get_snapshot(owner)['transactions'] == []


def test_expected_request_is_revalidated_after_waiting_for_draft_lock(db, actor, app, monkeypatch):
    _, owner = actor
    app.onboarding.handle(owner, '/start', NOW)
    request = draft_state(db, app, owner)['request_id']
    entered, release = Event(), Event()
    original_lock = app.onboarding._lock

    def delayed_lock(connection, owner_id):
        if current_thread().name.startswith('setup-guard'):
            entered.set()
            assert release.wait(10), 'Test did not release the waiting setup callback.'
        original_lock(connection, owner_id)

    monkeypatch.setattr(app.onboarding, '_lock', delayed_lock)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='setup-guard') as executor:
        callback = executor.submit(app.onboarding.handle, owner, 'setup:cancel', NOW,
                                   expected_request=request)
        try:
            if not entered.wait(2):
                callback.result(timeout=2)
                pytest.fail('The expected draft must be checked under the setup lock.')
            app.onboarding.handle(owner, '/restart', NOW)
            before = draft_state(db, app, owner)
            assert before['request_id'] != request
        finally:
            release.set()
        with pytest.raises(BudgetError) as rejected:
            callback.result(timeout=10)
    assert rejected.value.code == 'stale_setup'
    assert draft_state(db, app, owner) == before
    assert db.get_pending(owner) is None
    assert db.get_snapshot(owner)['transactions'] == []


async def test_all_setup_buttons_bind_canonical_draft_and_fit_telegram_bytes(db, actor, app):
    telegram_id, owner = actor
    replies = []
    for text in ('/start', '100', 'Travel', '40'):
        replies.append((await app.handle(private(telegram_id, text), NOW))[0])
    request = draft_state(db, app, owner)['request_id']
    assert str(UUID(request)) == request
    labels = {'Back': 'back', 'Cancel': 'cancel', 'Add more': 'more', 'Review': 'finish'}
    seen = set()
    for reply in replies:
        for row in reply['keyboard']:
            for item in row:
                action = labels[item['text']]
                assert item['data'] == f'setup:{action}:{request}'
                assert len(item['data'].encode('utf-8')) <= 64
                seen.add(action)
    assert seen == {'back', 'cancel', 'more', 'finish'}
    response = await app.handle(button(telegram_id, keyboard_data(replies[-1], 'Add more')), NOW)
    assert 'name' in response[0]['text'].lower()
    assert draft_state(db, app, owner)['step'] == 'name'
    response = await app.handle(button(telegram_id, keyboard_data(response[0], 'Cancel')), NOW)
    assert 'cancel' in response[0]['text'].lower()
    assert not app.onboarding.active(owner)
    assert db.get_snapshot(owner)['transactions'] == []


@pytest.mark.parametrize('label', ['Back', 'Cancel'])
async def test_expired_review_buttons_are_bound_and_cannot_reset_restarted_setup(db, actor, app, label):
    telegram_id, owner = actor
    for text in ('/start', '100', 'Travel', '40', 'review'):
        await app.handle(private(telegram_id, text), NOW)
    old = db.get_pending(owner)
    later = NOW + timedelta(minutes=31)
    expired = (await app.handle(private(telegram_id, '/start'), later))[0]
    assert 'expired' in expired['text'].lower()
    data = keyboard_data(expired, label)
    assert data == f'setup:{label.lower()}:{old["request_id"]}'
    assert len(data.encode('utf-8')) <= 64
    await app.handle(private(telegram_id, '/cancel'), later)
    await app.handle(private(telegram_id, '/start'), later)
    before = draft_state(db, app, owner)
    assert before['request_id'] != old['request_id']
    response = await app.handle(button(telegram_id, data), later)
    assert 'no longer current' in response[0]['text'].lower()
    assert draft_state(db, app, owner) == before
    assert db.get_pending(owner) is None
    assert app.onboarding.active(owner)
    assert db.get_snapshot(owner)['transactions'] == []


@pytest.mark.parametrize('data', [
    'setup:back', 'setup:cancel', 'setup:finish', 'setup:more',
    'setup:cancel:not-a-uuid', 'setup:cancel:{request}:extra',
    'setup:cancel:{{{request}}}', 'setup:cancel:{compact}',
    'setup:cancel:{upper}', 'setup:cancel:{request} ',
    'setup:cancel: {request}', 'setup:CANCEL:{request}',
    'setup:edit:{request}', 'setup:confirm:{request}',
])
async def test_unbound_or_malformed_setup_callback_cannot_mutate_current_review(db, actor, app, data):
    telegram_id, owner = actor
    await setup_review(app, telegram_id)
    pending = db.get_pending(owner)
    before = draft_state(db, app, owner)
    request = before['request_id']
    data = data.format(request=request, compact=request.replace('-', ''), upper=request.upper())
    response = await app.handle(button(telegram_id, data), NOW)
    assert db.get_pending(owner) == pending
    assert draft_state(db, app, owner) == before
    assert 'setup button' in response[0]['text'].lower()
    assert response[0]['keyboard'] == []
    assert db.get_snapshot(owner)['transactions'] == []


@pytest.mark.parametrize('label', ['Back', 'Cancel', 'Add more', 'Review'])
@pytest.mark.parametrize('source', ['stale', 'foreign'])
async def test_other_draft_setup_buttons_cannot_mutate_or_disclose_current_review(db, actor, app, label, source):
    telegram_id, owner = actor
    source_id, source_owner = telegram_id, owner
    if source == 'foreign':
        source_id = uuid4().int % (2**52 - 1) + 1
        source_owner = db.ensure_admin(source_id)
    for text in ('/start', '321.09', 'Secret bucket', '12.34'):
        reply = (await app.handle(private(source_id, text), NOW))[0]
    data = keyboard_data(reply, label)
    if source == 'stale':
        await app.handle(private(telegram_id, '/cancel'), NOW)
    source_before = draft_state(db, app, source_owner)
    await setup_review(app, telegram_id)
    pending = db.get_pending(owner)
    before = draft_state(db, app, owner)
    response = await app.handle(button(telegram_id, data), NOW)
    assert db.get_pending(owner) == pending
    assert draft_state(db, app, owner) == before
    if source == 'foreign':
        assert draft_state(db, app, source_owner) == source_before
    assert 'no longer current' in response[0]['text'].lower()
    assert '321.09' not in response[0]['text'] and 'Secret bucket' not in response[0]['text']
    assert response[0]['keyboard'] == []
    assert app.onboarding.active(owner)
    assert db.get_snapshot(owner)['transactions'] == []
    assert db.get_snapshot(source_owner)['transactions'] == []


@pytest.mark.parametrize('state_kind', ['absent', 'cancelled'])
@pytest.mark.parametrize('payload', ['cal:garbage', 'day:garbage', 'cal:2026-10', 'day:2026-10-06'])
async def test_nononboarded_calendar_callbacks_do_not_create_or_restart_setup(db, actor, app, state_kind, payload):
    telegram_id, owner = actor
    if state_kind == 'cancelled':
        await app.handle(private(telegram_id, '/start'), NOW)
        await app.handle(private(telegram_id, '/cancel'), NOW)
    before = draft_state(db, app, owner)
    snapshot, pending = db.get_snapshot(owner), db.get_pending(owner)
    reply = await app.handle(button(telegram_id, payload), NOW)
    assert '/start' in reply[0]['text'] and reply[0]['keyboard'] == []
    assert draft_state(db, app, owner) == before
    assert db.get_snapshot(owner) == snapshot and db.get_pending(owner) == pending
