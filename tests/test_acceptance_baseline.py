"""Frozen-main acceptance: real planner/store/reports, no provider or bot startup."""
import os
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from budget_bot.controller import BudgetController
from budget_bot.domain.errors import BudgetError
from budget_bot.services.reports import ReportService
from budget_bot.storage import BudgetStore
from budget_bot.storage.models import Posting, TransactionRevision
from budget_bot.telegram.calendar import day_view
from budget_bot.telegram.transport import TelegramTransport
from budget_bot.telegram.tables import flat, table_rows

NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)


@pytest.fixture
def store():
    assert os.environ.get('BUDGET_TEST_SCHEMA') == 'test_w8', 'Acceptance requires isolated test_w8'
    url = os.environ.get('BUDGET_TEST_DATABASE_URL')
    assert url, 'Supply the approved test database through the environment'
    db = BudgetStore(url, schema='test_w8')
    try:
        db.initialize()
    except Exception:
        db.close()
        pytest.fail('Approved test database initialization failed (connection details withheld)', pytrace=False)
    yield db
    db.close()


@pytest.fixture
def owner(store):
    # Unique identities avoid truncation and preserve other runs' audit evidence.
    return store.ensure_admin(uuid4().int % (2**62))


def commit(store, owner, actions):
    review = store.propose(owner, actions, NOW)
    result = store.confirm(owner, review['request_id'], review['revision'], NOW)
    assert result['status'] == 'committed'
    return result


def setup(store, owner):
    return commit(store, owner, [
        {'type': 'opening', 'amount_inr': '100'},
        {'type': 'create_bucket', 'name': 'Travel'},
        {'type': 'allocate', 'amount_inr': '50', 'bucket_name': 'Travel'},
    ])


def expense(amount='10'):
    return {'type': 'expense', 'amount_inr': amount, 'bucket_name': 'Travel', 'description': 'Metro'}


def test_review_cancel_and_duplicate_confirmation(store, owner):
    setup(store, owner)
    before = store.get_snapshot(owner)
    review = store.propose(owner, [expense()], NOW)
    assert store.get_snapshot(owner) == before
    store.cancel(owner, review['request_id'], NOW)
    assert store.get_snapshot(owner) == before
    with pytest.raises(BudgetError) as error:
        store.confirm(owner, review['request_id'], review['revision'], NOW)
    assert error.value.code == 'review_closed'
    review = store.propose(owner, [expense()], NOW)
    saved = store.confirm(owner, review['request_id'], review['revision'], NOW)
    assert store.confirm(owner, review['request_id'], review['revision'], NOW) == saved
    assert store.cancel(owner, review['request_id'], NOW) == saved
    assert saved['snapshot']['buckets']['Travel']['balance'] == 4000
    assert store.spending(owner, NOW.date(), NOW.date())['count'] == 1


def test_failed_combined_batch_leaves_no_pending_or_money(store, owner):
    setup(store, owner)
    before = store.get_snapshot(owner)
    with pytest.raises(BudgetError) as error:
        store.propose(owner, [
            {'type': 'income', 'amount_inr': '1', 'description': 'Gift'},
            {'type': 'allocate', 'amount_inr': '52', 'bucket_name': 'Travel'},
        ], NOW)
    assert error.value.code == 'insufficient_source_funds'
    assert store.get_snapshot(owner) == before
    assert store.get_pending(owner) is None


def test_negative_expense_does_not_implicitly_fund_bucket(store, owner):
    setup(store, owner)
    saved = commit(store, owner, [expense('60')])
    assert saved['snapshot']['pool'] == 5000
    assert saved['snapshot']['buckets']['Travel']['balance'] == -1000
    assert any('negative balance' in warning for warning in saved['warnings'])
    assert any('no implicit funding' in warning for warning in saved['warnings'])
    assert 'Total spent: ₹60.00' in ReportService(store).spending(owner, 'today', NOW)


def test_edit_stale_timezone_and_expiry_require_fresh_confirmation(store, owner):
    setup(store, owner)
    before = store.get_snapshot(owner)
    original = store.propose(owner, [expense()], NOW)
    edited = store.edit(owner, original['request_id'], [expense('12')], NOW)
    assert store.get_snapshot(owner) == before
    stale = store.confirm(owner, original['request_id'], original['revision'], NOW)
    assert stale['status'] == 'stale'
    assert stale['revision'] > edited['revision']
    assert store.get_snapshot(owner) == before
    store.set_timezone(owner, 'UTC')
    stale = store.confirm(owner, stale['request_id'], stale['revision'], NOW)
    assert stale['status'] == 'stale'
    assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 5000
    with pytest.raises(BudgetError) as error:
        store.confirm(owner, stale['request_id'], stale['revision'], NOW + timedelta(minutes=31))
    assert error.value.code == 'review_expired'
    assert store.get_snapshot(owner)['buckets']['Travel']['balance'] == 5000


def test_foreign_review_and_transaction_are_not_authorized(store, owner):
    setup(store, owner)
    other = store.redeem_invite(store.create_invite(owner, NOW), uuid4().int % (2**62), NOW)
    setup(store, other)
    review = store.propose(owner, [expense()], NOW)
    with pytest.raises(BudgetError) as error:
        store.confirm(other, review['request_id'], review['revision'], NOW)
    assert error.value.code == 'not_found'
    saved = store.confirm(owner, review['request_id'], review['revision'], NOW)
    transaction = next(t for t in saved['snapshot']['transactions'] if t['type'] == 'expense')
    with pytest.raises(BudgetError) as error:
        store.propose(other, [{'type': 'undo', 'transaction_id': transaction['id']}], NOW)
    assert error.value.code == 'unknown_transaction'
    assert store.spending(other, NOW.date(), NOW.date())['total'] == 0
    store.revoke_user(owner, _telegram_id(store, other), NOW)
    with pytest.raises(BudgetError) as error:
        store.get_snapshot(other)
    assert error.value.code == 'access_denied'


def _telegram_id(store, owner):
    from budget_bot.storage.models import User
    with store.engine.connect() as connection:
        return connection.scalar(select(User.telegram_id).where(User.id == owner))


def test_correct_undo_reports_calendar_and_preserved_audit(store, owner):
    setup(store, owner)
    saved = commit(store, owner, [expense()])
    transaction = next(t for t in saved['snapshot']['transactions'] if t['type'] == 'expense')
    commit(store, owner, [{'type': 'correct', 'transaction_id': transaction['id'],
                           'changes': {'amount_inr': '7', 'date_expression': '2026-10-05'}}])
    assert store.spending(owner, NOW.date(), NOW.date())['count'] == 0
    assert store.spending(owner, date(2026, 10, 5), date(2026, 10, 5))['total'] == 700
    assert any(r.split(maxsplit=2)[1:] == ['₹7.00', 'Metro'] for r in table_rows(day_view(store, owner, '2026-10-05')['text'], 'Bucket'))
    commit(store, owner, [{'type': 'undo', 'transaction_id': transaction['id']}])
    assert store.spending(owner, date(2026, 10, 1), date(2026, 10, 31))['count'] == 0
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(TransactionRevision).where(
            TransactionRevision.transaction_id == transaction['id'])) == 3
        assert connection.scalar(select(func.count()).select_from(Posting).where(
            Posting.transaction_id == transaction['id'])) == 3
        assert connection.scalar(text('SHOW lock_timeout')) == '5s'
        assert connection.scalar(text('SHOW statement_timeout')) == '15s'


def test_consumed_source_blocks_undo_and_future_expense(store, owner):
    saved = setup(store, owner)
    allocation = next(t for t in saved['snapshot']['transactions'] if t['type'] == 'allocate')
    commit(store, owner, [expense('50')])
    before = store.get_snapshot(owner)
    with pytest.raises(BudgetError) as error:
        store.propose(owner, [{'type': 'undo', 'transaction_id': allocation['id']}], NOW)
    assert error.value.code == 'insufficient_source_funds'
    with pytest.raises(BudgetError) as error:
        store.propose(owner, [{**expense(), 'date_expression': '2026-10-07'}], NOW)
    assert error.value.code == 'future_expense'
    assert store.get_snapshot(owner) == before


@pytest.mark.asyncio
async def test_actual_controller_report_identity_and_private_ingress(store, owner):
    setup(store, owner)
    controller = BudgetController(store, None, None, ReportService(store), None)
    telegram_id = _telegram_id(store, owner)
    payload = {'message': {'from': {'id': telegram_id, 'is_bot': False},
                          'chat': {'id': telegram_id, 'type': 'private'}, 'text': '/balance'}}
    assert 'Unallocated pool: ₹50.00' in flat((await controller.handle(payload, NOW))[0]['text'])
    payload['message']['chat']['type'] = 'group'
    assert await controller.handle(payload, NOW) == []


@pytest.mark.asyncio
async def test_durable_transport_retries_real_inbox_without_network(store):
    bot_id = str(uuid4())
    update_id = uuid4().int % (2**62)
    store.save_update(bot_id, update_id, {'update_id': update_id}, NOW)

    class OfflineBot:
        id = bot_id

        async def get_updates(self, **kwargs):
            return []

        async def send_message(self, **kwargs):
            self.sent = kwargs

    class Handler:
        failing = True

        async def handle(self, payload, now):
            if self.failing:
                raise RuntimeError('synthetic transient failure')
            return [{'chat_id': 1, 'text': 'Durable reply', 'keyboard': []}]

    bot, handler = OfflineBot(), Handler()
    transport = TelegramTransport(bot, store, handler)
    assert await transport.poll_once(NOW) == 0
    assert any(row['update_id'] == update_id for row in store.pending_updates())
    handler.failing = False
    assert await transport.poll_once(NOW) == 1
    assert await transport.deliver_once(NOW) == 1
    assert bot.sent['text'] == 'Durable reply'
    assert store.pending_outbox(NOW, bot_id=bot_id) == []


@pytest.mark.asyncio
async def test_calendar_command_is_wired_to_real_view(store, owner):
    setup(store, owner)
    controller = BudgetController(store, None, None, ReportService(store), None)
    telegram_id = _telegram_id(store, owner)
    payload = {'message': {'from': {'id': telegram_id, 'is_bot': False},
                          'chat': {'id': telegram_id, 'type': 'private'}, 'text': '/calendar'}}
    reply = (await controller.handle(payload, NOW))[0]
    assert reply['text'] == 'October 2026'
    assert reply['keyboard']
