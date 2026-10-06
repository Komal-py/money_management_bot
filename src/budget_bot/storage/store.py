"""Synchronous owner-serialized PostgreSQL persistence for the frozen W2 contract."""
import copy
import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import create_engine, event, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from budget_bot.domain.errors import BudgetError
from budget_bot.domain.planner import plan
from budget_bot.storage.models import (
    Account, Base, Batch, BatchResult, Cursor, Inbox, Invite, MetadataEvent, Outbox, Posting,
    Proposal, Request, TargetVersion, Transaction, TransactionRevision, User, install_guards,
)


def new_id():
    return str(uuid4())


def fail(code, message):
    raise BudgetError(code, message)


def aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        fail('invalid_datetime', 'An aware receipt timestamp is required.')
    return value.astimezone(timezone.utc)


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        fail('not_found', 'The requested record was not found.')


def checked_money(value):
    if isinstance(value, bool) or not isinstance(value, int) or not -(2**63) <= value < 2**63:
        fail('invalid_amount', 'Money must fit signed integer paise storage.')
    return value


class BudgetStore:
    def __init__(self, database_url: str, schema='budget'):
        if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', schema):
            fail('invalid_schema', 'Schema must be a safe PostgreSQL identifier.')
        self.schema = schema
        self.engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)
        if self.engine.dialect.name != 'postgresql':
            self.engine.dispose()
            fail('invalid_database', 'PostgreSQL is required.')

        @event.listens_for(self.engine, 'connect')
        def configure(dbapi_connection, _):
            old = dbapi_connection.autocommit
            dbapi_connection.autocommit = True
            try:
                with dbapi_connection.cursor() as cursor:
                    cursor.execute(f'SET search_path TO "{schema}"')
                    cursor.execute("SET lock_timeout TO '5s'")
                    cursor.execute("SET statement_timeout TO '15s'")
                    cursor.execute("SET idle_in_transaction_session_timeout TO '20s'")
            finally:
                dbapi_connection.autocommit = old

    def initialize(self):
        with self.engine.begin() as c:
            c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"'))
            Base.metadata.create_all(c)
            install_guards(c)

    def close(self):
        self.engine.dispose()

    def _owner(self, session, owner_id, lock=False, admin=False):
        query = select(User).where(User.id == identifier(owner_id))
        if lock:
            query = query.with_for_update()
        user = session.scalar(query)
        if user is None or not user.active:
            fail('access_denied', 'This account does not have active access.')
        if admin and not user.admin:
            fail('access_denied', 'Administrator access is required.')
        return user

    def ensure_admin(self, telegram_id: int):
        if isinstance(telegram_id, bool) or not isinstance(telegram_id, int):
            fail('invalid_user', 'A Telegram user ID is required.')
        with Session(self.engine) as s, s.begin():
            uid = new_id()
            s.execute(insert(User).values(id=uid, telegram_id=telegram_id, active=True, admin=True,
                                          timezone='Asia/Kolkata', onboarded=False, revision=0)
                      .on_conflict_do_nothing(index_elements=['telegram_id']))
            user = s.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            user.admin = True
            # Do not silently reactivate a revoked user.
            self._pool(s, user.id)
            return user.id

    def _pool(self, s, owner_id):
        account = s.scalar(select(Account).where(Account.owner_id == owner_id, Account.kind == 'pool'))
        if account is None:
            account = Account(id=new_id(), owner_id=owner_id, kind='pool', name='pool',
                              normalized_name='pool', balance=0, target=None)
            s.add(account)
            s.flush()
        return account

    @staticmethod
    def _user_dict(user):
        return {'owner_id': user.id, 'id': user.id, 'telegram_id': user.telegram_id,
                'active': user.active, 'admin': user.admin, 'timezone': user.timezone,
                'onboarded': user.onboarded}

    def get_user(self, telegram_id):
        with Session(self.engine) as s:
            user = s.scalar(select(User).where(User.telegram_id == telegram_id))
            return self._user_dict(user) if user else None

    def _snapshot(self, s, user):
        accounts = s.scalars(select(Account).where(Account.owner_id == user.id).order_by(Account.id)).all()
        transactions = s.scalars(select(Transaction).where(Transaction.owner_id == user.id)
                                 .order_by(Transaction.sequence)).all()
        targets = s.scalars(select(TargetVersion).where(TargetVersion.owner_id == user.id)
                            .order_by(TargetVersion.effective_month, TargetVersion.revision)).all()
        return {'owner_id': user.id, 'revision': user.revision, 'onboarded': user.onboarded,
                'timezone': user.timezone, 'pool': next((a.balance for a in accounts if a.kind == 'pool'), 0),
                'opening_date': user.opening_date.isoformat() if user.opening_date else None,
                'buckets': {a.name: {'id': a.id, 'balance': a.balance, 'target': a.target}
                            for a in accounts if a.kind == 'bucket'},
                'transactions': [copy.deepcopy(t.current) for t in transactions],
                'targets': [{'id': t.id, 'bucket_id': t.bucket_id,
                             'bucket_name': next(a.name for a in accounts if a.id == t.bucket_id),
                             'effective_month': t.effective_month.isoformat(),
                             'target': t.target, 'amount': t.target, 'revision': t.revision} for t in targets]}

    def get_snapshot(self, owner_id):
        # Lock gives a consistent multi-table snapshot at READ COMMITTED.
        with Session(self.engine) as s, s.begin():
            return self._snapshot(s, self._owner(s, owner_id, lock=True))

    def create_invite(self, admin_owner, now, ttl_hours=24):
        now = aware(now)
        if isinstance(ttl_hours, bool) or not isinstance(ttl_hours, (int, float)) or not 0 < ttl_hours <= 720:
            fail('invalid_invite', 'Invite lifetime must be between zero and 720 hours.')
        code = secrets.token_urlsafe(24)
        with Session(self.engine) as s, s.begin():
            admin = self._owner(s, admin_owner, lock=True, admin=True)
            s.add(Invite(digest=hashlib.sha256(code.encode()).hexdigest(), admin_owner=admin.id,
                         expires_at=now + timedelta(hours=ttl_hours)))
        return code

    def redeem_invite(self, code, telegram_id, now):
        now = aware(now)
        if not isinstance(code, str) or not code or len(code) > 200:
            fail('invalid_invite', 'The invite is invalid or expired.')
        if isinstance(telegram_id, bool) or not isinstance(telegram_id, int):
            fail('invalid_user', 'A Telegram user ID is required.')
        digest = hashlib.sha256(code.encode()).hexdigest()
        with Session(self.engine) as s, s.begin():
            # Serialize Telegram identity before invite consumption, including concurrent different invites.
            s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': telegram_id})
            if s.scalar(select(User).where(User.telegram_id == telegram_id)):
                fail('already_registered', 'This Telegram user is already registered.')
            invite = s.scalar(select(Invite).where(Invite.digest == digest).with_for_update())
            if invite is None or invite.redeemed_by or invite.expires_at <= now:
                fail('invalid_invite', 'The invite is invalid or expired.')
            user = User(id=new_id(), telegram_id=telegram_id, active=True, admin=False,
                        timezone='Asia/Kolkata', onboarded=False, revision=0)
            s.add(user)
            s.flush()
            self._pool(s, user.id)
            invite.redeemed_by, invite.redeemed_at = user.id, now
            return user.id

    def revoke_user(self, admin_owner, target_telegram_id, now):
        aware(now)
        with Session(self.engine) as s, s.begin():
            self._owner(s, admin_owner, admin=True)
            target = s.scalar(select(User).where(User.telegram_id == target_telegram_id).with_for_update())
            if target is None:
                fail('not_found', 'The user was not found.')
            if target.admin:
                fail('access_denied', 'Administrator access cannot be revoked here.')
            target.active = False
            target.revision += 1

    def list_access(self, admin_owner):
        with Session(self.engine) as s, s.begin():
            self._owner(s, admin_owner, admin=True)
            return [self._user_dict(u) for u in s.scalars(select(User).order_by(User.telegram_id))]

    def set_timezone(self, owner_id, zone):
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            fail('invalid_timezone', 'Choose a valid IANA timezone.')
        with Session(self.engine) as s, s.begin():
            user = self._owner(s, owner_id, lock=True)
            user.timezone = zone
            user.revision += 1

    @staticmethod
    def _review(request, status=None):
        return {'request_id': request.id, 'revision': request.revision, 'owner_id': request.owner_id,
                'status': status or request.status, 'actions': copy.deepcopy(request.actions),
                'plan': copy.deepcopy(request.plan), 'expires_at': request.expires_at.isoformat(),
                'state_revision': request.state_revision}

    def _save_proposal(self, s, request):
        s.flush()
        s.add(Proposal(id=new_id(), owner_id=request.owner_id, request_id=request.id,
                       revision=request.revision, review=self._review(request)))

    @staticmethod
    def _plan(snapshot, actions, received_at):
        if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
            fail('invalid_actions', 'A review must contain one to eight ordered actions.')
        return plan(copy.deepcopy(snapshot), copy.deepcopy(actions), received_at)

    def propose(self, owner_id, actions, received_at, request_id=None):
        received_at = aware(received_at)
        rid = identifier(request_id) if request_id is not None else new_id()
        with Session(self.engine) as s, s.begin():
            user = self._owner(s, owner_id, lock=True)
            existing = s.scalar(select(Request).where(Request.id == rid, Request.owner_id == user.id))
            if existing:
                return self._review(existing)
            pending = s.scalar(select(Request).where(Request.owner_id == user.id, Request.status == 'pending')
                               .with_for_update())
            if pending:
                if pending.expires_at > received_at:
                    fail('pending_review', 'Confirm, edit, or cancel the existing review first.')
                pending.status = 'expired'
                s.flush()
            snapshot = self._snapshot(s, user)
            proposed = self._plan(snapshot, actions, received_at)
            request = Request(id=rid, owner_id=user.id, status='pending', revision=1,
                              state_revision=user.revision, received_at=received_at,
                              expires_at=received_at + timedelta(minutes=30),
                              actions=copy.deepcopy(proposed['actions']), plan=proposed)
            s.add(request)
            self._save_proposal(s, request)
            return self._review(request)

    def get_pending(self, owner_id):
        with Session(self.engine) as s, s.begin():
            user = self._owner(s, owner_id, lock=True)
            request = s.scalar(select(Request).where(Request.owner_id == user.id, Request.status == 'pending'))
            return self._review(request) if request else None

    def _request(self, s, owner, request_id):
        request = s.scalar(select(Request).where(Request.id == identifier(request_id), Request.owner_id == owner.id)
                           .with_for_update())
        if request is None:
            fail('not_found', 'The review was not found.')
        return request

    @staticmethod
    def _result(s, request):
        batch = s.scalar(select(Batch).where(Batch.owner_id == request.owner_id, Batch.request_id == request.id))
        return copy.deepcopy(s.get(BatchResult, batch.id).result)

    @staticmethod
    def _pending(request, now):
        if request.status != 'pending':
            fail('review_closed', 'This review is no longer pending.')
        if request.expires_at <= now:
            fail('review_expired', 'This review has expired. Submit the changes again.')

    def edit(self, owner_id, request_id, actions, received_at):
        received_at = aware(received_at)
        with Session(self.engine) as s, s.begin():
            user = self._owner(s, owner_id, lock=True)
            request = self._request(s, user, request_id)
            self._pending(request, received_at)
            proposed = self._plan(self._snapshot(s, user), actions, received_at)
            request.actions, request.plan = proposed['actions'], proposed
            request.revision += 1
            request.state_revision = user.revision
            request.received_at = received_at
            request.expires_at = received_at + timedelta(minutes=30)
            self._save_proposal(s, request)
            return self._review(request)

    def cancel(self, owner_id, request_id, now):
        aware(now)
        with Session(self.engine) as s, s.begin():
            user = self._owner(s, owner_id, lock=True)
            request = self._request(s, user, request_id)
            if request.status == 'committed':
                return self._result(s, request)
            request.status = 'cancelled'
            return {'status': 'cancelled', 'request_id': request.id}

    @staticmethod
    def _retain_ids(proposed, previous):
        """Revalidation may mint UUIDs; an unchanged review retains its first assigned identities."""
        remap = {}
        old_new = [e['transaction'] for e in previous['events'] if e.get('previous') is None]
        fresh_new = [e['transaction'] for e in proposed['events'] if e.get('previous') is None]
        for fresh, old in zip(fresh_new, old_new):
            remap[fresh['id']] = old['id']
            if fresh.get('batch_id') and old.get('batch_id'):
                remap[fresh['batch_id']] = old['batch_id']
        for fresh, old in zip(proposed.get('metadata', []), previous.get('metadata', [])):
            if fresh.get('type') == old.get('type') and fresh.get('id') and old.get('id'):
                remap[fresh['id']] = old['id']
        for name, bucket in proposed['snapshot']['buckets'].items():
            prior = previous['snapshot']['buckets'].get(name)
            if prior:
                remap[bucket['id']] = prior['id']

        def replace(value):
            if isinstance(value, dict):
                return {k: replace(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace(v) for v in value]
            return remap.get(value, value) if isinstance(value, str) else value
        return replace(proposed)

    def confirm(self, owner_id, request_id, review_revision, now):
        now = aware(now)
        if isinstance(review_revision, bool) or not isinstance(review_revision, int):
            fail('invalid_revision', 'A valid review revision is required.')
        with Session(self.engine) as s, s.begin():
            user = self._owner(s, owner_id, lock=True)
            request = self._request(s, user, request_id)
            if request.status == 'committed':
                return self._result(s, request)
            self._pending(request, now)
            snapshot = self._snapshot(s, user)
            revalidated = self._plan(snapshot, request.actions, request.received_at)
            revalidated = self._retain_ids(revalidated, request.plan)
            if (request.revision != review_revision or request.state_revision != user.revision
                    or revalidated != request.plan):
                request.plan = revalidated
                request.actions = revalidated['actions']
                request.state_revision = user.revision
                request.revision += 1
                request.expires_at = now + timedelta(minutes=30)
                self._save_proposal(s, request)
                return self._review(request, status='stale')
            # Persist exactly the reviewed plan; never replace reviewed financial IDs on confirm.
            return self._commit(s, user, request, now)

    def _commit(self, s, user, request, now):
        proposed = copy.deepcopy(request.plan)
        projected = proposed['snapshot']
        accounts = s.scalars(select(Account).where(Account.owner_id == user.id).order_by(Account.id)
                             .with_for_update()).all()
        account_by_name = {a.name: a for a in accounts}
        if user.onboarded and not projected['onboarded']:
            fail('already_onboarded', 'Opening state cannot be reversed.')
        for name, bucket in projected['buckets'].items():
            if name not in account_by_name:
                if name != name.strip() or not 1 <= len(name) <= 60 or name.casefold() == 'pool':
                    fail('invalid_bucket', 'Choose a unique bucket name of 1 to 60 characters, not pool.')
                account = Account(id=identifier(bucket['id']), owner_id=user.id, kind='bucket', name=name,
                                  normalized_name=name.casefold(), balance=0, target=None)
                s.add(account)
                account_by_name[name] = account
        reviewed_batches = {e['transaction']['batch_id'] for e in proposed['events']
                            if e.get('previous') is None}
        if len(reviewed_batches) > 1:
            fail('invalid_plan', 'New transactions must belong to one reviewed batch.')
        batch_id = identifier(next(iter(reviewed_batches))) if reviewed_batches else new_id()
        batch = Batch(id=batch_id, owner_id=user.id, request_id=request.id, committed_at=now)
        s.add(batch)
        s.flush()
        transactions = {t.id: t for t in s.scalars(select(Transaction).where(Transaction.owner_id == user.id))}
        sequence = max((t.sequence for t in transactions.values()), default=0)
        for e in proposed['events']:
            state = copy.deepcopy(e['transaction'])
            tid = identifier(state['id'])
            existing = transactions.get(tid)
            if existing is None:
                if e.get('previous') is not None or state['revision'] != 1:
                    fail('invalid_plan', 'The transaction revision is not valid.')
                sequence += 1
                state['batch_id'] = batch_id
                existing = Transaction(id=tid, owner_id=user.id, originating_batch_id=batch_id,
                                       current=state, sequence=sequence)
                s.add(existing)
                transactions[tid] = existing
            else:
                if e.get('previous') != existing.current or state['revision'] != existing.current['revision'] + 1:
                    fail('invalid_plan', 'The transaction revision is not valid.')
                state['batch_id'] = existing.originating_batch_id
                existing.current = state
            s.flush()
            s.add(TransactionRevision(id=new_id(), owner_id=user.id, transaction_id=tid, batch_id=batch_id,
                                      revision=state['revision'], amount=checked_money(state['amount']),
                                      state=state, previous=e.get('previous')))
        s.flush()
        balances = {name: account.balance for name, account in account_by_name.items()}
        for p in proposed['postings']:
            account = account_by_name.get(p['account'])
            if account is None or p['transaction_id'] not in transactions:
                fail('invalid_plan', 'The posting account or transaction is not owner-scoped.')
            delta = checked_money(p['amount'])
            balances[account.name] = checked_money(balances[account.name] + delta)
            if delta < 0 and balances[account.name] < 0 and transactions[p['transaction_id']].current['type'] != 'expense':
                fail('insufficient_funds', 'Restore source funds before this allocation, transfer, or reversal.')
            s.add(Posting(id=new_id(), owner_id=user.id, batch_id=batch_id,
                          transaction_id=p['transaction_id'], account_id=account.id, amount=delta, created_at=now))
        if balances['pool'] != projected['pool']:
            fail('invalid_plan', 'Pool postings do not match the reviewed balance.')
        for name, bucket in projected['buckets'].items():
            if balances[name] != bucket['balance']:
                fail('invalid_plan', 'Bucket postings do not match the reviewed balance.')
        for name, account in account_by_name.items():
            account.balance = balances[name]
        local_date = request.received_at.astimezone(ZoneInfo(user.timezone)).date()
        for m in proposed['metadata']:
            s.add(MetadataEvent(id=new_id(), owner_id=user.id, batch_id=batch_id, value=m))
        # Preserve each ordered target action, even if the final value equals its previous value.
        for action in proposed['actions']:
            if action['type'] != 'set_target':
                continue
            name = next((n for n in projected['buckets']
                         if n.casefold() == action['bucket_name'].strip().casefold()), None)
            if name is None:
                fail('invalid_plan', 'The target bucket was not found.')
            account = account_by_name[name]
            target = projected['buckets'][name]['target']
            # Parse canonical amount exactly without owning W1's parser.
            if action.get('remove'):
                target = None
            elif 'amount_inr' in action:
                from budget_bot.domain.money import parse_money
                target = parse_money(action['amount_inr'])
            revision = (s.scalar(select(func.max(TargetVersion.revision))
                                 .where(TargetVersion.bucket_id == account.id)) or 0) + 1
            s.add(TargetVersion(id=new_id(), owner_id=user.id, bucket_id=account.id, batch_id=batch_id,
                                effective_month=local_date.replace(day=1), revision=revision,
                                target=checked_money(target) if target is not None else None))
            s.flush()
        for name, bucket in projected['buckets'].items():
            account_by_name[name].target = bucket['target']
        user.onboarded = projected['onboarded']
        user.opening_date = date.fromisoformat(projected['opening_date']) if projected['opening_date'] else None
        user.revision += 1
        request.status = 'committed'
        s.flush()
        result = {'status': 'committed', 'batch_id': batch_id, 'request_id': request.id,
                  'snapshot': self._snapshot(s, user), 'warnings': proposed['warnings'], 'summary': proposed['summary']}
        s.add(BatchResult(batch_id=batch_id, owner_id=user.id, result=result))
        return result

    def spending(self, owner_id, start: date, end: date, bucket_name=None):
        if not isinstance(start, date) or not isinstance(end, date) or start > end:
            fail('invalid_date', 'Choose a valid inclusive date range.')
        with Session(self.engine) as s, s.begin():
            user = self._owner(s, owner_id, lock=True)
            snapshot = self._snapshot(s, user)
            canonical = None
            if bucket_name is not None:
                canonical = next((n for n in snapshot['buckets'] if n.casefold() == bucket_name.strip().casefold()), None)
                if canonical is None:
                    fail('bucket_not_found', 'The bucket was not found.')
            expenses = [t for t in snapshot['transactions'] if t['type'] == 'expense' and t['active']
                        and start.isoformat() <= t['date'] <= end.isoformat()
                        and (canonical is None or t['bucket'] == canonical)]
            expenses.sort(key=lambda t: (t['date'], t['id']))
            by_bucket = {}
            for expense in expenses:
                by_bucket[expense['bucket']] = by_bucket.get(expense['bucket'], 0) + expense['amount']
            return {'start': start.isoformat(), 'end': end.isoformat(), 'by_bucket': by_bucket,
                    'total': sum(by_bucket.values()), 'count': len(expenses), 'expenses': expenses}

    def calendar_day(self, owner_id, day):
        return self.spending(owner_id, day, day)

    def save_update(self, bot_id, update_id, payload, now):
        now = aware(now)
        with Session(self.engine) as s, s.begin():
            s.execute(insert(Cursor).values(bot_id=str(bot_id), next_offset=update_id)
                      .on_conflict_do_nothing(index_elements=['bot_id']))
            cursor = s.scalar(select(Cursor).where(Cursor.bot_id == str(bot_id)).with_for_update())
            inserted = s.execute(insert(Inbox).values(bot_id=str(bot_id), update_id=update_id,
                                                     payload=copy.deepcopy(payload), received_at=now)
                                 .on_conflict_do_nothing(index_elements=['bot_id', 'update_id'])
                                 .returning(Inbox.update_id)).scalar_one_or_none()
            # Cursor is based on durable receipt, never handler completion. Gaps stay unacknowledged.
            while s.get(Inbox, (str(bot_id), cursor.next_offset)) is not None:
                cursor.next_offset += 1
            return inserted is not None

    def pending_updates(self, limit=100):
        with Session(self.engine) as s:
            rows = s.scalars(select(Inbox).where(Inbox.completed_at.is_(None))
                             .order_by(Inbox.received_at, Inbox.update_id).limit(limit)).all()
            return [{'bot_id': row.bot_id, 'update_id': row.update_id, 'payload': copy.deepcopy(row.payload),
                     'received_at': row.received_at.isoformat()} for row in rows]

    def complete_update(self, update_id, replies, now):
        now = aware(now)
        with Session(self.engine) as s, s.begin():
            rows = s.scalars(select(Inbox).where(Inbox.update_id == update_id).order_by(Inbox.bot_id)
                             .with_for_update()).all()
            if len(rows) != 1:
                fail('ambiguous_update', 'The update must identify exactly one bot inbox entry.')
            row = rows[0]
            if row.completed_at is not None:
                return
            for position, reply in enumerate(replies):
                s.add(Outbox(id=new_id(), bot_id=row.bot_id, update_id=update_id, position=position,
                             chat_id=reply['chat_id'], text=reply['text'], keyboard=copy.deepcopy(reply.get('keyboard')),
                             due_at=now, attempts=0))
            row.completed_at = now

    def polling_offset(self, bot_id):
        with Session(self.engine) as s:
            cursor = s.get(Cursor, str(bot_id))
            return cursor.next_offset if cursor else 0

    def pending_outbox(self, now, limit=20):
        now = aware(now)
        with Session(self.engine) as s, s.begin():
            rows = s.scalars(select(Outbox).where(Outbox.delivered_at.is_(None), Outbox.due_at <= now,
                                                  or_(Outbox.lease_until.is_(None), Outbox.lease_until <= now))
                             .order_by(Outbox.due_at, Outbox.id).limit(limit)
                             .with_for_update(skip_locked=True)).all()
            result = []
            for row in rows:
                row.lease_token, row.lease_until = new_id(), now + timedelta(seconds=60)
                row.attempts += 1
                result.append({'id': row.id, 'chat_id': row.chat_id, 'text': row.text,
                               'keyboard': copy.deepcopy(row.keyboard), 'lease_token': row.lease_token})
            return result

    def _finish_outbox(self, id, lease_token, now, success):
        now = aware(now)
        with Session(self.engine) as s, s.begin():
            row = s.scalar(select(Outbox).where(Outbox.id == identifier(id)).with_for_update())
            if (row is None or row.delivered_at is not None or row.lease_token is None
                    or row.lease_token != lease_token or row.lease_until is None or row.lease_until <= now):
                return False
            row.lease_token, row.lease_until = None, None
            if success:
                row.delivered_at = now
            else:
                row.due_at = now + timedelta(seconds=min(300, 2 ** min(row.attempts, 8)))
            return True

    def ack_outbox(self, id, lease_token, now):
        return self._finish_outbox(id, lease_token, now, True)

    def fail_outbox(self, id, lease_token, now):
        return self._finish_outbox(id, lease_token, now, False)
