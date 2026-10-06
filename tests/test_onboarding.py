"""W3 integration tests: real PostgreSQL, BudgetStore and domain planner."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from budget_bot.domain.errors import BudgetError
from budget_bot.services.onboarding import OnboardingService
from budget_bot.storage import BudgetStore

NOW = datetime(2026, 10, 6, 18, 45, tzinfo=timezone.utc)
pytestmark = pytest.mark.postgres


@pytest.fixture(scope='module')
def store():
    assert os.environ.get('BUDGET_TEST_DATABASE_URL'), 'Supply test database settings externally.'
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w3', 'Only test_w3 is authorized.'
    db = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w3')
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def owner(store):
    return store.ensure_admin(uuid4().int % (2**52))


@pytest.fixture
def service(store):
    result = OnboardingService(store)
    result.initialize()
    return result


def assert_envelope(result):
    assert set(result) == {'text', 'keyboard', 'review', 'done'}
    assert isinstance(result['text'], str)
    assert isinstance(result['done'], bool)
    assert all(set(button) == {'text', 'data'} for row in result['keyboard'] for button in row)


def test_opening_is_one_question_and_durable_without_financial_writes(store, owner, service):
    before = store.get_snapshot(owner)
    assert not service.active(owner)
    result = service.handle(owner, '/start', NOW)
    assert_envelope(result)
    assert result['text'].count('?') == 1
    assert 'opening' in result['text'].lower()
    assert '0' in result['text']
    assert result['review'] is None and not result['done']
    assert service.active(owner)
    reopened = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w3')
    try:
        resumed = OnboardingService(reopened)
        resumed.initialize()
        assert resumed.active(owner)
        assert resumed.handle(owner, '/start', NOW) == result
    finally:
        reopened.close()
    assert store.get_snapshot(owner) == before
    with store.engine.connect() as connection:
        assert connection.execute(text('SELECT current_schema()')).scalar_one() == 'test_w3'
    assert 'setup_states' in inspect(store.engine).get_table_names(schema='test_w3')


@pytest.mark.parametrize('opening,allocation', [('100.50', '40.25'), ('0', '0')])
def test_guided_flow_review_replay_and_external_confirmation(store, owner, service, opening, allocation):
    before = store.get_snapshot(owner)
    service.handle(owner, '/start', NOW)
    result = service.handle(owner, opening, NOW)
    assert 'name' in result['text'].lower()
    result = service.handle(owner, ' Travel ', NOW)
    assert 'allocate' in result['text'].lower()
    result = service.handle(owner, allocation, NOW)
    assert 'review' in result['text'].lower()
    assert store.get_snapshot(owner) == before
    review = service.handle(owner, 'setup:finish', NOW)['review']
    assert review is not None
    assert [a['type'] for a in review['actions']] == (
        ['opening', 'create_bucket', 'allocate'] if allocation != '0' else ['opening', 'create_bucket'])
    assert review['actions'][1]['name'] == 'Travel'
    assert store.get_snapshot(owner) == before
    resumed = OnboardingService(store)
    for command in ('/start', 'setup:finish'):
        replay = resumed.handle(owner, command, NOW)['review']
        assert datetime.fromisoformat(replay['expires_at']) == datetime.fromisoformat(review['expires_at'])
        assert {k: v for k, v in replay.items() if k != 'expires_at'} == {
            k: v for k, v in review.items() if k != 'expires_at'}
    assert resumed.active(owner)
    committed = store.confirm(owner, review['request_id'], review['revision'], NOW)
    assert committed['status'] == 'committed'
    assert store.confirm(owner, review['request_id'], review['revision'], NOW) == committed
    assert not resumed.active(owner)
    assert resumed.handle(owner, '/start', NOW)['done']
    assert resumed.handle(owner, '/restart', NOW)['done']
    assert len([t for t in store.get_snapshot(owner)['transactions'] if t['type'] == 'opening']) == 1


@pytest.mark.parametrize('value', ['-1', '1.001', '1e2', 'NaN', '', '1,00', True, 1.5])
def test_invalid_opening_does_not_advance(store, owner, service, value):
    prompt = service.handle(owner, '/start', NOW)
    with pytest.raises(BudgetError):
        service.handle(owner, value, NOW)
    assert service.handle(owner, '/start', NOW) == prompt
    assert not store.get_snapshot(owner)['onboarded']


@pytest.mark.parametrize('name', ['', '   ', 'a' * 61, None, True, ' Pool '])
def test_invalid_name_does_not_advance(owner, service, name):
    service.handle(owner, '/start', NOW)
    prompt = service.handle(owner, '10', NOW)
    with pytest.raises(BudgetError) as error:
        service.handle(owner, name, NOW)
    assert error.value.code == 'invalid_bucket'
    assert service.handle(owner, '/start', NOW) == prompt


def test_duplicate_names_and_source_funds(store, owner, service):
    service.handle(owner, '/start', NOW)
    service.handle(owner, '10', NOW)
    prompt = service.handle(owner, 'Travel', NOW)
    with pytest.raises(BudgetError, match='10.00'):
        service.handle(owner, '10.01', NOW)
    assert service.handle(owner, '/start', NOW) == prompt
    service.handle(owner, '7', NOW)
    service.handle(owner, 'setup:more', NOW)
    with pytest.raises(BudgetError) as error:
        service.handle(owner, ' travel ', NOW)
    assert error.value.code == 'duplicate_bucket'
    service.handle(owner, 'Food', NOW)
    with pytest.raises(BudgetError, match='3.00'):
        service.handle(owner, '4', NOW)
    service.handle(owner, '3', NOW)
    review = service.handle(owner, 'review', NOW)['review']
    assert review['plan']['snapshot']['pool'] == 0
    assert store.get_snapshot(owner)['buckets'] == {}


def test_eight_action_bound_allows_zero_final_allocation(owner, service):
    service.handle(owner, '/start', NOW)
    service.handle(owner, '100', NOW)
    for name in ('A', 'B', 'C'):
        service.handle(owner, name, NOW)
        service.handle(owner, '1', NOW)
        service.handle(owner, 'setup:more', NOW)
    service.handle(owner, 'D', NOW)
    with pytest.raises(BudgetError) as error:
        service.handle(owner, '1', NOW)
    assert error.value.code == 'invalid_actions'
    service.handle(owner, '0', NOW)
    with pytest.raises(BudgetError) as error:
        service.handle(owner, 'setup:more', NOW)
    assert error.value.code == 'invalid_actions'
    review = service.handle(owner, 'setup:finish', NOW)['review']
    assert len(review['actions']) == 8


def test_back_cancel_edit_restart_and_stale_review(store, owner, service):
    opening = service.handle(owner, '/start', NOW)
    assert service.back(owner, NOW) == opening
    name = service.handle(owner, '100', NOW)
    service.handle(owner, 'Travel', NOW)
    assert service.back(owner, NOW) == name
    allocation = service.handle(owner, 'Food', NOW)
    service.handle(owner, '20', NOW)
    assert service.back(owner, NOW) == allocation
    service.handle(owner, '30', NOW)
    old = service.handle(owner, 'review', NOW)['review']
    assert 'opening' in service.handle(owner, 'Edit', NOW)['text'].lower()
    assert store.get_pending(owner) is None
    with pytest.raises(BudgetError):
        store.confirm(owner, old['request_id'], old['revision'], NOW)
    service.handle(owner, '0', NOW)
    service.handle(owner, 'Empty', NOW)
    service.handle(owner, '0', NOW)
    new = service.handle(owner, 'review', NOW)['review']
    assert new['request_id'] != old['request_id']
    cancelled = service.cancel(owner, NOW)
    assert cancelled['done'] and not service.active(owner)
    assert service.cancel(owner, NOW) == cancelled
    assert service.handle(owner, 'setup:finish', NOW) == cancelled
    assert store.get_pending(owner) is None
    restarted = service.handle(owner, '/start', NOW)
    assert {key: value for key, value in restarted.items() if key != 'keyboard'} == {
        key: value for key, value in opening.items() if key != 'keyboard'}
    first_request = opening['keyboard'][0][0]['data'].split(':')[-1]
    next_request = restarted['keyboard'][0][0]['data'].split(':')[-1]
    assert next_request != first_request
    service.handle(owner, '40', NOW)
    reset = service.handle(owner, '/restart', NOW)
    assert {key: value for key, value in reset.items() if key != 'keyboard'} == {
        key: value for key, value in opening.items() if key != 'keyboard'}
    assert reset['keyboard'][0][0]['data'].split(':')[-1] not in {first_request, next_request}
    assert store.get_snapshot(owner)['buckets'] == {}


def ready(service, owner):
    for answer in ('/start', '10', 'Travel', '2'):
        service.handle(owner, answer, NOW)


def test_interrupted_handoff_recovers_same_proposal(store, owner, service, monkeypatch):
    ready(service, owner)

    def interrupted(*args):
        raise RuntimeError('simulated interruption after proposal commit')

    monkeypatch.setattr(service, '_save', interrupted)
    with pytest.raises(RuntimeError, match='simulated interruption'):
        service.handle(owner, 'setup:finish', NOW)
    pending = store.get_pending(owner)
    assert pending is not None
    resumed = OnboardingService(store)
    assert resumed.handle(owner, '/start', NOW)['review']['request_id'] == pending['request_id']
    resumed.back(owner, NOW)
    assert store.get_pending(owner) is None
    review = resumed.handle(owner, 'setup:finish', NOW)['review']
    assert review['request_id'] != pending['request_id']


def test_external_cancel_and_expiry_lifecycle(store, owner, service):
    ready(service, owner)
    review = service.handle(owner, 'review', NOW)['review']
    expired = service.handle(owner, '/start', NOW + timedelta(minutes=31))
    assert expired['review'] is None and 'expired' in expired['text'].lower()
    service.handle(owner, '/restart', NOW + timedelta(minutes=31))
    assert store.get_pending(owner) is None
    for answer in ('10', 'Travel', '2', 'review'):
        result = service.handle(owner, answer, NOW + timedelta(minutes=31))
    assert result['review']['request_id'] != review['request_id']
    store.cancel(owner, result['review']['request_id'], NOW + timedelta(minutes=31))
    assert not service.active(owner)
    assert service.handle(owner, '/start', NOW + timedelta(minutes=31))['review'] is None


def test_owner_isolation_and_revocation_at_each_step(store, owner, service):
    code = store.create_invite(owner, NOW)
    telegram_id = uuid4().int % (2**52)
    other = store.redeem_invite(code, telegram_id, NOW)
    ready(service, owner)
    opening = service.handle(other, '/start', NOW)
    assert 'opening' in opening['text'].lower()
    service.cancel(other, NOW)
    review = service.handle(owner, 'review', NOW)['review']
    assert review['owner_id'] == owner
    with pytest.raises(BudgetError):
        store.confirm(other, review['request_id'], review['revision'], NOW)
    store.revoke_user(owner, telegram_id, NOW)
    for operation in (lambda: service.active(other), lambda: service.handle(other, '/start', NOW),
                      lambda: service.back(other, NOW), lambda: service.cancel(other, NOW)):
        with pytest.raises(BudgetError) as error:
            operation()
        assert error.value.code == 'access_denied'


def test_misplaced_callback_is_not_a_bucket_name(owner, service):
    service.handle(owner, '/start', NOW)
    prompt = service.handle(owner, '10', NOW)
    assert service.handle(owner, 'setup:finish', NOW) == prompt
    assert service.handle(owner, 'setup:more', NOW) == prompt


def test_saved_partial_draft_is_resumable(store, owner, service):
    with store.engine.begin() as connection:
        connection.execute(service.states.insert().values(owner_id=owner, state={'step': 'opening'}, updated_at=NOW))
    for answer in ('/start', '0', 'Travel', '0', 'review'):
        result = service.handle(owner, answer, NOW)
    assert result['review'] is not None


def test_access_invite_list_revoke_without_financial_reads(store, owner, monkeypatch):
    from budget_bot.services.access import AccessService

    service = AccessService(store)

    def forbidden(*args, **kwargs):
        pytest.fail('Access service must not read or mutate financial records')

    for method in ('get_snapshot', 'propose', 'confirm', 'spending'):
        monkeypatch.setattr(store, method, forbidden)
    invite = service.handle_admin(owner, 'invite', [], NOW)
    code = invite['text'].split('/start ', 1)[1].split()[0]
    telegram_id = uuid4().int % (2**52)
    other = store.redeem_invite(code, telegram_id, NOW)
    assert store.get_user(telegram_id)['owner_id'] == other
    with pytest.raises(BudgetError):
        store.redeem_invite(code, uuid4().int % (2**52), NOW)
    listed = service.handle_admin(owner, 'users', [], NOW)
    assert str(telegram_id) in listed['text']
    assert all(word not in listed['text'].lower() for word in ('balance', 'bucket', 'onboarded', 'timezone'))
    for command, args in [('invite', []), ('users', []), ('revoke', [str(telegram_id)])]:
        with pytest.raises(BudgetError) as error:
            service.handle_admin(other, command, args, NOW)
        assert error.value.code == 'access_denied'
    result = service.handle_admin(owner, 'revoke', [str(telegram_id)], NOW)
    assert 'revoked' in result['text'].lower()
    assert store.get_user(telegram_id)['active'] is False
    assert f'{telegram_id}: revoked' in service.handle_admin(owner, 'users', [], NOW)['text']
    with pytest.raises(BudgetError):
        service.handle_admin(other, 'users', [], NOW)
    admin_id = next(user['telegram_id'] for user in store.list_access(owner) if user['owner_id'] == owner)
    with pytest.raises(BudgetError):
        service.handle_admin(owner, 'revoke', [str(admin_id)], NOW)


@pytest.mark.parametrize('command,args', [
    ('invite', ['extra']), ('users', ['extra']), ('revoke', []), ('revoke', ['1', '2']),
    ('revoke', ['1.0']), ('revoke', ['-1']), ('revoke', [True]), ('revoke', ['9' * 30]),
    ('balance', []), ('invite', None),
])
def test_access_rejects_invalid_commands_and_ids(store, owner, command, args):
    from budget_bot.services.access import AccessService

    with pytest.raises(BudgetError):
        AccessService(store).handle_admin(owner, command, args, NOW)


def test_concurrent_finish_uses_one_shared_proposal(store, owner, service):
    ready(service, owner)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(OnboardingService(store).handle, owner, 'setup:finish', NOW)
                   for _ in range(2)]
        reviews = [future.result(timeout=20)['review'] for future in futures]
    assert reviews[0]['request_id'] == reviews[1]['request_id']
    assert reviews[0]['plan'] == reviews[1]['plan']
    assert not store.get_snapshot(owner)['onboarded']
    assert store.get_pending(owner)['request_id'] == reviews[0]['request_id']


def test_reconstruct_every_step_and_no_setup_confirmation(store, owner, service):
    for answer in ('/start', '10', 'Travel', '2', 'review'):
        response = service.handle(owner, answer, NOW)
        reopened = BudgetStore(os.environ['BUDGET_TEST_DATABASE_URL'], schema='test_w3')
        try:
            rebuilt = OnboardingService(reopened)
            assert rebuilt.active(owner)
            replay = rebuilt.handle(owner, '/start', NOW)
            assert replay['text'] == response['text']
            assert replay['keyboard'] == response['keyboard']
        finally:
            reopened.close()
    for answer in ('Confirm', 'confirm', 'setup:confirm'):
        response = service.handle(owner, answer, NOW)
        assert response['review'] is not None
        assert response['keyboard'] == []
        assert not store.get_snapshot(owner)['onboarded']


def test_initialize_only_owns_setup_table(store, service, monkeypatch):
    before = set(inspect(store.engine).get_table_names(schema='test_w3'))

    def forbidden():
        pytest.fail('Onboarding initialize must not initialize the shared store')

    monkeypatch.setattr(store, 'initialize', forbidden)
    service.initialize()
    assert set(service.metadata.tables) == {'test_w3.setup_states'}
    assert set(inspect(store.engine).get_table_names(schema='test_w3')) == before


@pytest.mark.parametrize('operation', ['handle', 'back', 'cancel'])
def test_setup_rejects_naive_timestamps(owner, service, operation):
    with pytest.raises(BudgetError) as error:
        if operation == 'handle':
            service.handle(owner, '/start', NOW.replace(tzinfo=None))
        else:
            getattr(service, operation)(owner, NOW.replace(tzinfo=None))
    assert error.value.code == 'invalid_datetime'
    assert not service.active(owner)


def test_confirmation_wins_during_review_resume(store, owner, service, monkeypatch):
    ready(service, owner)
    review = service.handle(owner, 'review', NOW)['review']
    original = store.propose

    def confirm_then_read(*args, **kwargs):
        store.confirm(owner, review['request_id'], review['revision'], NOW)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, 'propose', confirm_then_read)
    result = service.handle(owner, '/start', NOW)
    assert result['done'] and result['review'] is None
    assert 'complete' in result['text'].lower()
    assert store.get_snapshot(owner)['onboarded']


def test_cancel_draft_leaves_unrelated_review_untouched(store, owner, service):
    service.handle(owner, '/start', NOW)
    other = store.propose(owner, [{'type': 'create_bucket', 'name': 'Independent'}], NOW)
    service.cancel(owner, NOW)
    assert store.get_pending(owner)['request_id'] == other['request_id']


@pytest.mark.parametrize('answers', [('/start',), ('/start', '10'),
                                    ('/start', '10', 'Travel'), ('/start', '10', 'Travel', '2'),
                                    ('/start', '10', 'Travel', '2', 'review')])
def test_revocation_blocks_every_draft_phase(store, owner, service, answers):
    telegram_id = uuid4().int % (2**52)
    member = store.redeem_invite(store.create_invite(owner, NOW), telegram_id, NOW)
    for answer in answers:
        service.handle(member, answer, NOW)
    with store.engine.connect() as connection:
        before = service._load(connection, member)
    store.revoke_user(owner, telegram_id, NOW)
    for answer in ('/start', '10', 'setup:finish', 'setup:more', 'Back', 'Cancel', 'Edit', '/restart'):
        with pytest.raises(BudgetError) as error:
            service.handle(member, answer, NOW)
        assert error.value.code == 'access_denied'
    with store.engine.connect() as connection:
        assert service._load(connection, member) == before
