"""Application-owned PostgreSQL tables. All connections use an isolated search_path."""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'bot_users'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(100), default='Asia/Kolkata')
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    opening_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (CheckConstraint('revision >= 0'),)


class Invite(Base):
    __tablename__ = 'invites'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_owner: Mapped[str] = mapped_column(ForeignKey('bot_users.id'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_by: Mapped[str | None] = mapped_column(ForeignKey('bot_users.id'), nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Account(Base):
    __tablename__ = 'accounts'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('bot_users.id'))
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(60))
    normalized_name: Mapped[str] = mapped_column(String(120))
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    target: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (
        UniqueConstraint('owner_id', 'id'), UniqueConstraint('owner_id', 'normalized_name'),
        CheckConstraint("kind IN ('pool','bucket')"),
        CheckConstraint("kind <> 'pool' OR balance >= 0"),
        CheckConstraint('target IS NULL OR target > 0'),
        CheckConstraint("kind <> 'pool' OR (normalized_name = 'pool' AND target IS NULL)"),
        Index('one_pool_per_owner', 'owner_id', unique=True, postgresql_where=sql_text("kind = 'pool'")),
    )


class Request(Base):
    __tablename__ = 'requests'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('bot_users.id'))
    status: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    state_revision: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actions: Mapped[list] = mapped_column(JSONB)
    plan: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint('owner_id', 'id'),
        CheckConstraint("status IN ('pending','committed','cancelled','expired')"),
        CheckConstraint('revision > 0'),
        Index('one_pending_per_owner', 'owner_id', unique=True, postgresql_where=sql_text("status = 'pending'")),
    )


class Proposal(Base):
    __tablename__ = 'proposals'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    request_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    revision: Mapped[int] = mapped_column(Integer)
    review: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'request_id'], ['requests.owner_id', 'requests.id']),
        UniqueConstraint('request_id', 'revision'),
    )


class Batch(Base):
    __tablename__ = 'financial_batches'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    request_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint('owner_id', 'id'), UniqueConstraint('owner_id', 'request_id'),
        ForeignKeyConstraint(['owner_id', 'request_id'], ['requests.owner_id', 'requests.id']),
    )


class BatchResult(Base):
    __tablename__ = 'committed_results'
    batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    result: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'batch_id'], ['financial_batches.owner_id', 'financial_batches.id']),
    )


class Transaction(Base):
    __tablename__ = 'logical_transactions'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('bot_users.id'))
    originating_batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    current: Mapped[dict] = mapped_column(JSONB)
    sequence: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint('owner_id', 'id'), UniqueConstraint('owner_id', 'sequence'),
        ForeignKeyConstraint(['owner_id', 'originating_batch_id'], ['financial_batches.owner_id', 'financial_batches.id']),
        Index('single_opening_per_owner', 'owner_id', unique=True,
              postgresql_where=sql_text("current ->> 'type' = 'opening'")),
    )


class TransactionRevision(Base):
    __tablename__ = 'transaction_revisions'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    transaction_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    revision: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[dict] = mapped_column(JSONB)
    previous: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'transaction_id'], ['logical_transactions.owner_id', 'logical_transactions.id']),
        ForeignKeyConstraint(['owner_id', 'batch_id'], ['financial_batches.owner_id', 'financial_batches.id']),
        UniqueConstraint('transaction_id', 'revision'), CheckConstraint('revision > 0'),
        CheckConstraint('amount >= 0'),
    )


class Posting(Base):
    __tablename__ = 'ledger_postings'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    transaction_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    account_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'batch_id'], ['financial_batches.owner_id', 'financial_batches.id']),
        ForeignKeyConstraint(['owner_id', 'transaction_id'], ['logical_transactions.owner_id', 'logical_transactions.id']),
        ForeignKeyConstraint(['owner_id', 'account_id'], ['accounts.owner_id', 'accounts.id']),
    )


class TargetVersion(Base):
    __tablename__ = 'target_versions'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    bucket_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    effective_month: Mapped[object] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer)
    target: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'bucket_id'], ['accounts.owner_id', 'accounts.id']),
        ForeignKeyConstraint(['owner_id', 'batch_id'], ['financial_batches.owner_id', 'financial_batches.id']),
        UniqueConstraint('bucket_id', 'effective_month', 'revision', name='target_versions_month_revision_key'),
        CheckConstraint('target IS NULL OR target > 0'),
    )


class MetadataEvent(Base):
    __tablename__ = 'metadata_events'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    value: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'batch_id'], ['financial_batches.owner_id', 'financial_batches.id']),
    )


class Inbox(Base):
    __tablename__ = 'telegram_inbox'
    bot_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Cursor(Base):
    __tablename__ = 'polling_cursor'
    bot_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    next_offset: Mapped[int] = mapped_column(BigInteger)


class Outbox(Base):
    __tablename__ = 'telegram_outbox'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(100))
    update_id: Mapped[int] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    keyboard: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        ForeignKeyConstraint(['bot_id', 'update_id'], ['telegram_inbox.bot_id', 'telegram_inbox.update_id']),
        UniqueConstraint('bot_id', 'update_id', 'position'),
        Index('outbox_due', 'due_at', postgresql_where=sql_text('delivered_at IS NULL')),
    )


IMMUTABLE_TABLES = (
    'financial_batches', 'proposals', 'transaction_revisions', 'ledger_postings',
    'target_versions', 'metadata_events', 'committed_results',
)


def install_guards(connection):
    """DB-enforced append-only history, even for direct SQL outside BudgetStore."""
    connection.execute(sql_text('''
        CREATE OR REPLACE FUNCTION reject_history_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'budget audit history is immutable' USING ERRCODE = '23514';
        END $$
    '''))
    for table in IMMUTABLE_TABLES:
        connection.execute(sql_text(f'DROP TRIGGER IF EXISTS immutable_history ON "{table}"'))
        connection.execute(sql_text(f'''CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON "{table}"
                                   FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()'''))
    connection.execute(sql_text('''
        CREATE OR REPLACE FUNCTION guard_posting_source() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE account_kind text; transaction_kind text; available numeric;
        BEGIN
            SELECT kind INTO account_kind FROM accounts
                WHERE id = NEW.account_id AND owner_id = NEW.owner_id FOR UPDATE;
            SELECT current->>'type' INTO transaction_kind FROM logical_transactions
                WHERE id = NEW.transaction_id AND owner_id = NEW.owner_id;
            IF NEW.amount < 0 AND (account_kind = 'pool' OR transaction_kind <> 'expense') THEN
                SELECT COALESCE(sum(amount), 0) INTO available FROM ledger_postings
                    WHERE account_id = NEW.account_id AND owner_id = NEW.owner_id;
                IF available + NEW.amount < 0 THEN
                    RAISE EXCEPTION 'budget source funds are insufficient' USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    '''))
    connection.execute(sql_text('DROP TRIGGER IF EXISTS source_funds ON ledger_postings'))
    connection.execute(sql_text('''CREATE TRIGGER source_funds BEFORE INSERT ON ledger_postings
                                 FOR EACH ROW EXECUTE FUNCTION guard_posting_source()'''))
    connection.execute(sql_text('''
        CREATE OR REPLACE FUNCTION guard_current_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE live_state jsonb; audit_state jsonb;
        BEGIN
            SELECT current INTO live_state FROM logical_transactions WHERE id = NEW.id;
            SELECT state INTO audit_state FROM transaction_revisions
                WHERE transaction_id = NEW.id ORDER BY revision DESC LIMIT 1;
            IF live_state IS DISTINCT FROM audit_state THEN
                RAISE EXCEPTION 'budget current state requires an audit revision' USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END $$
    '''))
    connection.execute(sql_text('DROP TRIGGER IF EXISTS audited_current ON logical_transactions'))
    connection.execute(sql_text('''CREATE CONSTRAINT TRIGGER audited_current
                                 AFTER INSERT OR UPDATE ON logical_transactions
                                 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                                 EXECUTE FUNCTION guard_current_revision()'''))
    connection.execute(sql_text('''
        CREATE OR REPLACE FUNCTION guard_revision_chain() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE prior jsonb; prior_revision integer;
        BEGIN
            SELECT revision, state INTO prior_revision, prior FROM transaction_revisions
                WHERE transaction_id = NEW.transaction_id ORDER BY revision DESC LIMIT 1;
            IF NEW.state->>'id' IS DISTINCT FROM NEW.transaction_id::text
               OR (NEW.state->>'revision')::integer IS DISTINCT FROM NEW.revision
               OR (NEW.state->>'amount')::bigint IS DISTINCT FROM NEW.amount
               OR NEW.revision <> COALESCE(prior_revision, 0) + 1
               OR (prior_revision IS NOT NULL AND NEW.previous IS DISTINCT FROM prior)
               OR (prior_revision IS NOT NULL AND NEW.state->>'type' IS DISTINCT FROM prior->>'type') THEN
                RAISE EXCEPTION 'budget audit revision chain is invalid' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    '''))
    connection.execute(sql_text('DROP TRIGGER IF EXISTS revision_chain ON transaction_revisions'))
    connection.execute(sql_text('''CREATE TRIGGER revision_chain BEFORE INSERT ON transaction_revisions
                                 FOR EACH ROW EXECUTE FUNCTION guard_revision_chain()'''))
    connection.execute(sql_text('''
        CREATE OR REPLACE FUNCTION guard_opening_state() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.onboarded AND (NOT NEW.onboarded OR NEW.opening_date IS DISTINCT FROM OLD.opening_date) THEN
                RAISE EXCEPTION 'budget opening state is permanent' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    '''))
    connection.execute(sql_text('DROP TRIGGER IF EXISTS permanent_opening ON bot_users'))
    connection.execute(sql_text('''CREATE TRIGGER permanent_opening BEFORE UPDATE ON bot_users
                                 FOR EACH ROW EXECUTE FUNCTION guard_opening_state()'''))
