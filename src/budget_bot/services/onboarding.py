"""Owner-scoped, durable setup drafts; financial authority stays in BudgetStore."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, MetaData, Table, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.schema import CreateSchema

from budget_bot.domain.errors import BudgetError
from budget_bot.domain.money import format_money, parse_money


class OnboardingService:
    """Durable opening -> name -> allocation -> more -> review lifecycle.

    initialize() is explicit and must follow store initialization. active()
    reports draft/review routing, not authorization (revoked owners raise).
    Cancel leaves a tombstone; /start resumes or restarts a cancelled draft.
    Edit restarts questions and invalidates the old proposal; Back revisits the
    prior question. Only the parent/W6 may render confirmation buttons/confirm.
    """
    def __init__(self, store):
        self.store = store
        self.metadata = MetaData(schema=store.schema)
        self.states = Table(
            'setup_states', self.metadata,
            Column('owner_id', UUID(as_uuid=False), primary_key=True),
            Column('state', JSONB, nullable=False),
            Column('updated_at', DateTime(timezone=True), nullable=False),
        )

    def initialize(self):
        """Create only this service's schema/table, never provision a database."""
        with self.store.engine.begin() as connection:
            connection.execute(CreateSchema(self.store.schema, if_not_exists=True))
            self.metadata.create_all(connection)

    @staticmethod
    def _now(now):
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise BudgetError('invalid_datetime', 'An aware receipt timestamp is required.')

    def _lock(self, connection, owner_id):
        connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                           {'key': f'budget-setup:{self.store.schema}:{owner_id}'})

    def _load(self, connection, owner_id):
        return connection.execute(select(self.states.c.state).where(
            self.states.c.owner_id == owner_id)).scalar_one_or_none()

    def _save(self, connection, owner_id, state, now):
        connection.execute(update(self.states).where(self.states.c.owner_id == owner_id)
                           .values(state=state, updated_at=now))

    def active(self, owner_id):
        snapshot = self.store.get_snapshot(owner_id)
        if snapshot['onboarded']:
            return False
        with self.store.engine.connect() as connection:
            state = self._load(connection, owner_id)
        if state is None or state['step'] == 'cancelled':
            return False
        if state['step'] == 'review':
            pending = self.store.get_pending(owner_id)
            return pending is not None and pending['request_id'] == state['request_id']
        return True

    @staticmethod
    def _response(message, keyboard=None, review=None, done=False):
        return {'text': message, 'keyboard': keyboard or [], 'review': review, 'done': done}

    def _prompt(self, state):
        if state['step'] == 'cancelled':
            return self._response('Setup cancelled. Use /start to begin again.', done=True)
        messages = {
            'opening': 'What is your opening available money in INR (0 is okay)?',
            'name': 'What name would you like for your bucket?',
            'allocation': f'How much INR should we allocate to {state.get("name", "this bucket")} (0 to skip)?',
            'more': 'Would you like to add another bucket or review setup?',
        }
        request = state['request_id']
        keyboard = [[{'text': 'Back', 'data': f'setup:back:{request}'},
                     {'text': 'Cancel', 'data': f'setup:cancel:{request}'}]]
        if state['step'] == 'more':
            keyboard.insert(0, [{'text': 'Add more', 'data': f'setup:more:{request}'},
                                {'text': 'Review', 'data': f'setup:finish:{request}'}])
        return self._response(messages[state['step']], keyboard)

    @staticmethod
    def _new_state():
        return {'step': 'opening', 'buckets': [], 'request_id': str(uuid4())}

    @staticmethod
    def _amount(paise):
        return f'{paise // 100}.{paise % 100:02d}'

    def _actions(self, state):
        actions = [{'type': 'opening', 'amount_inr': self._amount(state['opening'])}]
        for bucket in state['buckets']:
            actions.append({'type': 'create_bucket', 'name': bucket['name']})
            if bucket['amount']:
                actions.append({'type': 'allocate', 'bucket_name': bucket['name'],
                                'amount_inr': self._amount(bucket['amount'])})
        return actions

    def _review(self, owner_id, state, now):
        # The setup transaction holds ONLY its independent advisory lock. Never
        # lock store owner rows here: store methods use their own transactions.
        # The request ID was saved with the draft, so retry after an interrupted
        # handoff retrieves the same proposal, including its financial identities.
        review = self.store.propose(owner_id, self._actions(state), now, request_id=state['request_id'])
        if review['status'] == 'committed':
            return self._response('Setup is complete. Open balances or the calendar.', done=True)
        if review['status'] in ('cancelled', 'expired'):
            state['step'] = 'cancelled'
            return self._prompt(state)
        state['step'] = 'review'
        if datetime.fromisoformat(review['expires_at']) <= now:
            request = state['request_id']
            return self._response('Setup review expired. Use Back to review again, Edit, /restart or Cancel.',
                                  [[{'text': 'Back', 'data': f'setup:back:{request}'},
                                    {'text': 'Cancel', 'data': f'setup:cancel:{request}'}]])
        return self._response('Review setup before confirming. No money has been recorded.', review=review)

    def back(self, owner_id, now):
        return self.handle(owner_id, 'setup:back', now)

    def cancel(self, owner_id, now):
        return self.handle(owner_id, 'setup:cancel', now)

    def _cancel_review(self, owner_id, state, now):
        pending = self.store.get_pending(owner_id)
        if pending and pending['request_id'] == state['request_id']:
            result = self.store.cancel(owner_id, state['request_id'], now)
            if result['status'] == 'committed':
                return True
        # Confirmation may have won before get_pending returned.
        return self.store.get_snapshot(owner_id)['onboarded']

    @staticmethod
    def _back(state):
        if state['step'] == 'more':
            bucket = state['buckets'].pop()
            state['name'] = bucket['name']
            state['step'] = 'allocation'
        elif state['step'] == 'allocation':
            state.pop('name', None)
            state['step'] = 'name'
        elif state['step'] == 'name':
            state['step'] = 'more' if state['buckets'] else 'opening'

    @staticmethod
    def _mark(state, operation):
        if operation is not None:
            state['last_operation'] = operation
        return state

    def handle(self, owner_id, text, now, *, expected_request=None, operation=None):
        """Handle text, optionally fencing a button to its draft under the setup lock."""
        self._now(now)
        with self.store.engine.begin() as connection:
            self._lock(connection, owner_id)
            snapshot = self.store.get_snapshot(owner_id)
            state = self._load(connection, owner_id)
            if expected_request is not None and (
                    snapshot['onboarded'] or not state or state['step'] == 'cancelled'
                    or state.get('request_id') != expected_request):
                raise BudgetError('stale_setup', 'That setup button is no longer current. Use /start for setup.')
            if snapshot['onboarded']:
                return self._response('Setup is complete. Open balances or the calendar.', done=True)
            if operation is not None and state is not None and state.get('last_operation') == operation:
                # Inbox retry of an already applied answer: replay the resulting prompt only.
                return self._prompt(state)
            if state is None:
                state = self._new_state()
                if text in ('setup:cancel', '/cancel', 'Cancel', 'cancel'):
                    state['step'] = 'cancelled'
                connection.execute(insert(self.states).values(
                    owner_id=owner_id, state=self._mark(state, operation), updated_at=now))
                return self._prompt(state)
            if 'request_id' not in state:
                state.setdefault('buckets', [])
                state['request_id'] = str(uuid4())
                self._save(connection, owner_id, self._mark(state, operation), now)
            # Recover a store proposal whose commit outlived the draft write.
            if state['step'] == 'more':
                pending = self.store.get_pending(owner_id)
                if pending and pending['request_id'] == state['request_id']:
                    state['step'] = 'review'
            command = text.casefold() if isinstance(text, str) else None
            if command in ('setup:cancel', '/cancel', 'cancel', '/restart', 'restart',
                           'edit', '/edit', 'setup:edit'):
                if self._cancel_review(owner_id, state, now):
                    return self._response('Setup is complete. Open balances or the calendar.', done=True)
                state = self._new_state()
                if command in ('setup:cancel', '/cancel', 'cancel'):
                    state['step'] = 'cancelled'
                self._save(connection, owner_id, self._mark(state, operation), now)
                return self._prompt(state)
            if state['step'] == 'cancelled':
                if command == '/start':
                    state = self._new_state()
                    self._save(connection, owner_id, self._mark(state, operation), now)
                return self._prompt(state)
            if command in ('setup:back', '/back', 'back'):
                if state['step'] == 'review':
                    if self._cancel_review(owner_id, state, now):
                        return self._response('Setup is complete. Open balances or the calendar.', done=True)
                    state['request_id'] = str(uuid4())
                    state['step'] = 'more'
                else:
                    self._back(state)
                self._save(connection, owner_id, self._mark(state, operation), now)
                return self._prompt(state)
            if state['step'] == 'review':
                result = self._review(owner_id, state, now)
                self._save(connection, owner_id, self._mark(state, operation), now)
                return result
            if text == '/start':
                return self._prompt(state)
            if isinstance(text, str) and text.startswith('setup:') and (
                    state['step'] != 'more' or text not in ('setup:finish', 'setup:more')):
                return self._prompt(state)
            if state['step'] == 'opening':
                state['opening'] = parse_money(text, allow_zero=True)
                state['step'] = 'name'
            elif state['step'] == 'name':
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 60:
                    raise BudgetError('invalid_bucket', 'Bucket names must contain 1–60 characters.')
                if text.strip().casefold() == 'pool':
                    raise BudgetError('invalid_bucket', 'The pool account name is reserved.')
                names = list(snapshot['buckets']) + [b['name'] for b in state['buckets']]
                if any(name.casefold() == text.strip().casefold() for name in names):
                    raise BudgetError('duplicate_bucket', 'Choose a different bucket name; this name already exists.')
                state['name'] = text.strip()
                state['step'] = 'allocation'
            elif state['step'] == 'allocation':
                amount = parse_money(text, allow_zero=True)
                remaining = state['opening'] - sum(b['amount'] for b in state['buckets'])
                if amount > remaining:
                    raise BudgetError('insufficient_funds', f'Only {format_money(remaining)} remains available.')
                if len(self._actions(state)) + 1 + bool(amount) > 8:
                    raise BudgetError('invalid_actions', 'Setup allows eight actions. Enter 0 or go Back, then review.')
                state['buckets'].append({'name': state.pop('name'), 'amount': amount})
                state['step'] = 'more'
            elif text in ('setup:finish', 'review'):
                result = self._review(owner_id, state, now)
                self._save(connection, owner_id, self._mark(state, operation), now)
                return result
            elif text in ('setup:more', 'more'):
                if len(self._actions(state)) >= 8:
                    raise BudgetError('invalid_actions', 'Setup allows eight actions. Review now; add buckets later.')
                state['step'] = 'name'
            self._save(connection, owner_id, self._mark(state, operation), now)
            return self._prompt(state)
