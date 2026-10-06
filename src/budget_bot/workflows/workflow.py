"""LangGraph owns conversation state; BudgetStore alone owns financial writes."""
import asyncio
import copy
import hashlib
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import TypedDict
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg import Connection, sql
from psycopg.rows import dict_row
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from budget_bot.domain.errors import BudgetError
from budget_bot.domain.planner import FIELDS
from budget_bot.domain.money import parse_money
from budget_bot.storage.models import Batch, BatchResult, Request
from .render import render_result, render_review


def fail(code, message):
    raise BudgetError(code, message)


def identity(value):
    # Validate without normalizing externally supplied identifiers.
    if not isinstance(value, str):
        fail('invalid_identity', 'A UUID identity is required.')
    try:
        parsed = UUID(value)
    except ValueError:
        fail('invalid_identity', 'A UUID identity is required.')
    if str(parsed) != value:
        fail('invalid_identity', 'A canonical UUID identity is required.')
    return value


def timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        fail('invalid_datetime', 'An aware receipt timestamp is required.')
    return value.astimezone(timezone.utc).isoformat()


@asynccontextmanager
async def postgres_checkpointer(database_url, schema='workflow'):
    """Library-managed checkpoint tables, restricted to the selected schema.

    Use ``async with postgres_checkpointer(...) as saver`` for the app lifespan.
    SQLAlchemy URLs are converted without logging or exposing connection details.
    """
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', schema):
        fail('invalid_schema', 'Choose a safe workflow schema identifier.')
    url = make_url(database_url)
    if url.get_backend_name() != 'postgresql':
        fail('invalid_database', 'PostgreSQL is required.')
    kwargs = {'host': url.host, 'port': url.port or 5432, 'dbname': url.database,
              'user': url.username, 'password': url.password, 'connect_timeout': 10,
              'autocommit': True, 'prepare_threshold': 0, 'row_factory': dict_row,
              'options': '-c lock_timeout=5000 -c statement_timeout=15000'}
    kwargs.update({k: v for k, v in url.query.items() if k in {'sslmode', 'sslrootcert'}})
    conn = await asyncio.to_thread(Connection.connect, **kwargs)
    try:
        await asyncio.to_thread(conn.execute,
                                sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
        await asyncio.to_thread(conn.execute,
                                sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        saver = ThreadedPostgresSaver(conn)
        await asyncio.to_thread(saver.setup)
        yield saver
    finally:
        await asyncio.to_thread(conn.close)


class ThreadedPostgresSaver(PostgresSaver):
    """Supported PostgresSaver SQL, offloaded for Windows Proactor compatibility."""

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for row in rows:
            yield row

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=''):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        await asyncio.to_thread(self.delete_thread, thread_id)


class State(TypedDict, total=False):
    owner: str
    request: str
    received: str
    actions: list
    review: dict | None
    output: dict
    question: str | None
    mode: str
    original: str
    candidates: list
    selection_index: int
    bucket_index: int
    reply: dict
    route: str
    edit: bool
    nl: bool
    funding_resolved: bool
    field_index: int
    field_name: str


class SessionState(TypedDict, total=False):
    active: str | None


class BudgetWorkflow:
    def __init__(self, store, interpreter=None, checkpointer=None):
        self.store = store
        self.interpreter = interpreter
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        # Parent can replace these with W5 deterministic renderers, without AI dependencies.
        self.render_review = render_review
        self.render_result = render_result
        graph = StateGraph(State)
        graph.add_node('prepare', self._prepare)
        graph.add_node('wait', self._wait)
        graph.add_node('apply', self._apply)
        graph.add_edge(START, 'prepare')
        graph.add_conditional_edges('prepare', lambda s: s['route'], {'wait': 'wait', 'end': END})
        graph.add_edge('wait', 'apply')
        graph.add_conditional_edges('apply', lambda s: s['route'],
                                    {'prepare': 'prepare', 'wait': 'wait', 'end': END})
        self.graph = graph.compile(checkpointer=self.checkpointer)
        session = StateGraph(SessionState)
        session.add_node('save', lambda s: s)
        session.add_edge(START, 'save')
        session.add_edge('save', END)
        self.sessions = session.compile(checkpointer=self.checkpointer)

    @staticmethod
    def config(owner_id, request_id):
        return {'configurable': {'thread_id': f'budget:{identity(owner_id)}:{identity(request_id)}'}}

    @staticmethod
    def _session_config(owner):
        return {'configurable': {'thread_id': f'budget:{owner}:session'}}

    async def _active(self, owner):
        state = await self.sessions.aget_state(self._session_config(owner))
        return state.values.get('active')

    async def _set_active(self, owner, request):
        await self.sessions.ainvoke({'active': request}, self._session_config(owner))

    @asynccontextmanager
    async def _locked(self, owner):
        identity(owner)
        key = int.from_bytes(hashlib.sha256(f'budget-workflow:{owner}'.encode()).digest()[:8],
                             'big', signed=True)
        conn = await asyncio.to_thread(self.store.engine.connect)
        conn = conn.execution_options(isolation_level='AUTOCOMMIT')
        acquired = False
        try:
            for _ in range(100):
                acquired = await asyncio.to_thread(
                    lambda: conn.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}).scalar_one())
                if acquired:
                    break
                await asyncio.sleep(0.05)
            if not acquired:
                fail('workflow_busy', 'This conversation is busy. Try again.')
            await asyncio.to_thread(self.store.get_snapshot, owner)
            yield
        finally:
            try:
                if acquired:
                    await asyncio.to_thread(
                        conn.execute, text('SELECT pg_advisory_unlock(:key)'), {'key': key})
            finally:
                await asyncio.to_thread(conn.close)

    def _stored(self, owner, rid):
        # Read only: recovery never bypasses the store's mutation or confirmation authority.
        with Session(self.store.engine) as session:
            request = session.scalar(select(Request).where(Request.owner_id == owner, Request.id == rid))
            if request is None:
                return None
            if request.status == 'committed':
                batch = session.scalar(select(Batch).where(Batch.owner_id == owner, Batch.request_id == rid))
                return self._result(copy.deepcopy(session.get(BatchResult, batch.id).result))
            if request.status != 'pending':
                return self._result({'status': request.status, 'request_id': rid})
            review = {'owner_id': owner, 'request_id': rid, 'revision': request.revision,
                      'state_revision': request.state_revision, 'status': request.status,
                      'actions': copy.deepcopy(request.actions), 'plan': copy.deepcopy(request.plan),
                      'expires_at': request.expires_at.astimezone(timezone.utc).isoformat()}
            return self._review(review)

    @staticmethod
    def _message(message, **extra):
        return {'text': message, 'keyboard': [], 'review': None, 'result': None, **extra}

    def _review(self, review):
        rid, revision = review['request_id'], review['revision']
        return {'text': self.render_review(review), 'keyboard': [[
            {'text': d.title(), 'data': f'rev:{rid}:{revision}:{d}'} for d in ('confirm', 'edit', 'cancel')
        ]], 'review': review, 'result': None}

    def _result(self, result):
        if result['status'] == 'expired':
            return self._message('This review expired. Submit the changes again.', result=result)
        return self._message(self.render_result(result), result=result)

    async def _start(self, owner, actions, received, rid, **extra):
        config = self.config(owner, rid)
        existing = await asyncio.to_thread(self._stored, owner, rid)
        if existing:
            return existing
        saved = await self.graph.aget_state(config)
        if saved.values:
            if saved.next and not any(task.interrupts for task in saved.tasks):
                saved_values = await self.graph.ainvoke(None, config)
                return saved_values['output']
            return saved.values['output']
        active = await self._active(owner)
        if active and active != rid:
            old = await self.graph.aget_state(self.config(owner, active))
            terminal = await asyncio.to_thread(self._stored, owner, active)
            expired = (terminal and terminal['review'] and
                       datetime.fromisoformat(terminal['review']['expires_at']) <= datetime.fromisoformat(received))
            if old.next and not (terminal and terminal['result']) and not expired:
                fail('pending_review', 'Finish or cancel the current interaction first.')
        await self._set_active(owner, rid)
        state = {'owner': owner, 'request': rid, 'received': received, 'actions': actions,
                 'review': None, 'question': None, 'mode': 'actions', 'edit': False,
                 'nl': False, **extra}
        try:
            value = await self.graph.ainvoke(state, config)
        except BudgetError as error:
            await self._set_active(owner, None)
            if error.code == 'provider_unavailable':
                return self._message(error.message + ' Use /help for commands.')
            raise
        return value['output']

    async def submit(self, owner_id, actions, received_at, request_id=None):
        received = timestamp(received_at)
        rid = identity(request_id) if request_id is not None else str(uuid4())
        async with self._locked(owner_id):
            return await self._start(owner_id, copy.deepcopy(actions), received, rid)

    async def natural_language(self, owner_id, text, received_at):
        received = timestamp(received_at)
        async with self._locked(owner_id):
            if self.interpreter is None:
                return self._message('Natural-language interpretation is unavailable. Use /help for commands.')
            rid = str(uuid4())
            return await self._start(owner_id, [], received, rid, nl=True, original=text, mode='interpret')

    async def answer(self, owner_id, text, now):
        stamp = timestamp(now)
        async with self._locked(owner_id):
            rid = await self._active(owner_id)
            if not rid:
                fail('no_question', 'There is no current question to answer.')
            config = self.config(owner_id, rid)
            state = await self.graph.aget_state(config)
            if not state.next or not state.values.get('question'):
                fail('no_question', 'There is no current question to answer.')
            if text.strip().casefold() in {'cancel', '/cancel'}:
                return await self._resume(config, {'decision': 'cancel', 'now': stamp})
            return await self._resume(config, {'answer': text, 'now': stamp})

    async def decide(self, owner_id, request_id, revision, decision, now, edited_actions=None):
        identity(request_id)
        stamp = timestamp(now)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            fail('invalid_revision', 'Choose a valid review revision.')
        if decision not in {'confirm', 'edit', 'cancel'}:
            fail('invalid_decision', 'Choose Confirm, Edit, or Cancel.')
        async with self._locked(owner_id):
            stored = await asyncio.to_thread(self._stored, owner_id, request_id)
            if stored is None:
                fail('not_found', 'The review was not found.')
            if stored['result']:
                return stored
            review = stored['review']
            # Edit/cancel do not accept a stale callback: store APIs lack a revision argument.
            if decision != 'confirm' and review['revision'] != revision:
                return stored
            config = self.config(owner_id, request_id)
            state = await self.graph.aget_state(config)
            if not state.values:
                # A proposal can survive loss of checkpoints or originate from guided setup.
                await self._set_active(owner_id, request_id)
                await self.graph.ainvoke({'owner': owner_id, 'request': request_id, 'received': stamp,
                                         'actions': review['actions'], 'review': review, 'question': None,
                                         'mode': 'actions', 'edit': False, 'nl': False}, config)
            elif state.next and not any(task.interrupts for task in state.tasks):
                value = await self.graph.ainvoke(None, config)
                if value['output']['result']:
                    return value['output']
            return await self._resume(config, {'decision': decision, 'revision': revision, 'now': stamp,
                                               'edited_actions': copy.deepcopy(edited_actions)})

    async def _resume(self, config, reply):
        value = await self.graph.ainvoke(Command(resume=reply), config)
        return value['output']

    async def _interpret(self, state, snapshot, answer=None):
        if self.interpreter is None:
            fail('provider_unavailable', 'Natural-language interpretation is unavailable. Use commands.')
        prompt = state.get('original', '')
        if answer is not None:
            prompt += '\nCurrent question: ' + (state.get('question') or 'Edit the current review')
            prompt += '\nOwner answer: ' + answer
        output = await self.interpreter.interpret(prompt, list(snapshot['buckets']),
                                                   datetime.fromisoformat(state['received']), snapshot['timezone'])
        required = {'schema_version', 'kind', 'actions', 'missing_fields', 'clarification_question', 'query'}
        if (not isinstance(output, dict) or set(output) != required or output['schema_version'] != 1
                or isinstance(output['schema_version'], bool)
                or output['kind'] not in {'mutation', 'query', 'clarification', 'unsupported'}
                or not isinstance(output['actions'], list) or len(output['actions']) > 8
                or not isinstance(output['missing_fields'], list)
                or any(not isinstance(f, str) for f in output['missing_fields'])
                or (output['clarification_question'] is not None
                    and not isinstance(output['clarification_question'], str))):
            fail('invalid_model_output', 'The interpretation was invalid. Use commands or try again.')
        if output['kind'] == 'mutation' and (not output['actions'] or output['missing_fields']):
            fail('invalid_model_output', 'The mutation is incomplete. Use commands or try again.')
        for action in output['actions']:
            if (not isinstance(action, dict) or action.get('type') not in FIELDS
                    or set(action) - ({'type'} | FIELDS[action['type']])):
                fail('invalid_model_output', 'The interpretation contained unsupported action fields.')
        return output

    def _query(self, query, snapshot):
        fields = {'report', 'period', 'start', 'end', 'bucket_name'}
        if (not isinstance(query, dict) or set(query) != fields
                or query['report'] not in {'balances', 'spending', 'calendar'}
                or query['period'] not in {'today', 'week', 'month', 'range'}):
            fail('invalid_model_output', 'Choose a supported budget report.')
        q = copy.deepcopy(query)
        if q['period'] == 'range':
            try:
                start, end = date.fromisoformat(q['start']), date.fromisoformat(q['end'])
            except (TypeError, ValueError):
                fail('invalid_model_output', 'Choose valid report dates.')
            if start > end or start.isoformat() != q['start'] or end.isoformat() != q['end']:
                fail('invalid_model_output', 'Choose an ordered ISO report date range.')
        elif q['start'] is not None or q['end'] is not None:
            fail('invalid_model_output', 'Only range reports accept explicit dates.')
        if q['bucket_name'] is not None:
            if not isinstance(q['bucket_name'], str):
                fail('invalid_model_output', 'Select an existing bucket.')
            canonical = next((n for n in snapshot['buckets'] if n.casefold() == q['bucket_name'].casefold()), None)
            if canonical is None:
                fail('unknown_bucket', 'Select an existing bucket.')
            q['bucket_name'] = canonical
        return self._message('Report request validated.', query=q)

    async def _prepare(self, state):
        try:
            return await self._prepare_actions(state)
        except BudgetError as error:
            if state.get('review'):
                return {'output': self._review(state['review']) | {
                    'text': error.message + '\n' + self.render_review(state['review'])},
                    'edit': False, 'mode': 'review', 'question': None, 'route': 'wait'}
            raise

    async def _prepare_actions(self, state):
        snapshot = await asyncio.to_thread(self.store.get_snapshot, state['owner'])
        if state['mode'] == 'interpret':
            output = await self._interpret(state, snapshot)
            original = state.get('original', '').casefold()
            ambiguous = (re.search(r'\b(add|put|top\s+up)\b', original)
                         and re.search(r'\b(to|into)\b', original)
                         and any(name.casefold() in original for name in snapshot['buckets'])
                         and not re.search(r'\b(pool|allocate|allocation|income|salary|earned|existing|new)\b', original))
            if ambiguous and output['kind'] == 'mutation' and not state.get('funding_resolved'):
                question = 'Is this allocation from existing pool money, or new income followed by allocation?'
                return {'question': question, 'mode': 'interpret', 'actions': [],
                        'output': self._message(question), 'route': 'wait'}
            if output['kind'] == 'query':
                return {'output': self._query(output['query'], snapshot), 'route': 'end'}
            if output['kind'] == 'unsupported':
                return {'output': self._message('This request is outside supported budgeting actions.'), 'route': 'end'}
            if output['kind'] == 'clarification':
                question = output['clarification_question'] or 'Please clarify the missing budgeting details.'
                return {'question': question, 'mode': 'interpret', 'actions': output['actions'],
                        'output': self._message(question), 'route': 'wait'}
            state = {**state, 'actions': output['actions'], 'mode': 'actions'}
        actions = copy.deepcopy(state['actions'])
        if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
            fail('invalid_actions', 'A review needs one to eight ordered actions.')
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                fail('invalid_actions', 'Each action must be an object.')
            required = []
            if action.get('type') in {'opening', 'income', 'allocate', 'transfer', 'expense'}:
                required.append('amount_inr')
            if action.get('type') in {'income', 'expense'}:
                required.append('description')
            if action.get('type') == 'create_bucket':
                required.append('name')
            for field in required:
                if action.get(field) is None or action.get(field) == '':
                    question = f"Enter {field.replace('_', ' ')} for action {index + 1} ({action['type']})."
                    return {'actions': actions, 'mode': 'field', 'field_index': index, 'field_name': field,
                            'question': question, 'output': self._message(question), 'route': 'wait'}
            if state.get('nl') and action.get('type') == 'opening':
                fail('invalid_actions', 'Opening money is available only through guided setup.')
            if state.get('nl') and action.get('type') in {'undo', 'correct'} and not action.get('_selected'):
                candidates = [t for t in snapshot['transactions'] if t['active'] and t['type'] != 'opening']
                if not candidates:
                    fail('unknown_transaction', 'There are no active transactions to select.')
                # No model IDs, batch IDs or last selector can select a record on the owner's behalf.
                for field in ('transaction_id', 'batch_id', 'last'):
                    action.pop(field, None)
                question = 'Select the transaction ID to undo or correct:\n' + '\n'.join(
                    f"{t['id']} | {t['date']} | {t['type']} | {t['description']} | {t['bucket']} | {t['amount']} paise"
                    for t in candidates)
                return {'actions': actions, 'question': question, 'mode': 'select',
                        'candidates': [t['id'] for t in candidates], 'selection_index': index,
                        'output': self._message(question), 'route': 'wait'}
            if action.get('type') == 'expense' and not action.get('bucket_name'):
                names = list(snapshot['buckets'])
                question = 'Choose a bucket for this expense: ' + ', '.join(names)
                if not names:
                    question = 'No buckets exist. Cancel and create a named bucket, or reply create: <name>.'
                return {'actions': actions, 'question': question, 'mode': 'bucket', 'bucket_index': index,
                        'output': self._message(question), 'route': 'wait'}
        clean = [{k: v for k, v in a.items() if k != '_selected'} for a in actions]
        if state.get('review') is not None and not state.get('edit'):
            review = state['review']
        elif state.get('edit'):
            review = await asyncio.to_thread(self.store.edit, state['owner'], state['request'], clean,
                                             datetime.fromisoformat(state['received']))
        else:
            review = await asyncio.to_thread(self.store.propose, state['owner'], clean,
                                             datetime.fromisoformat(state['received']), state['request'])
        return {'actions': actions, 'review': review, 'question': None, 'mode': 'review',
                'output': self._review(review), 'route': 'wait', 'edit': False}

    async def _wait(self, state):
        reply = interrupt({'owner_id': state['owner'], 'request_id': state['request'],
                           'revision': (state.get('review') or {}).get('revision'),
                           'question': state.get('question'), 'output': state['output']})
        return {'reply': reply}

    async def _apply(self, state):
        try:
            return await self._apply_reply(state)
        except BudgetError as error:
            output = copy.deepcopy(state['output'])
            output['text'] = error.message + '\n' + output['text']
            return {'output': output, 'route': 'wait'}

    async def _apply_reply(self, state):
        reply = state['reply']
        now = datetime.fromisoformat(reply['now'])
        if reply.get('decision') == 'cancel':
            if state.get('review'):
                result = await asyncio.to_thread(self.store.cancel, state['owner'], state['request'], now)
            else:
                result = {'status': 'cancelled', 'request_id': state['request']}
            return {'output': self._result(result), 'question': None, 'route': 'end'}
        if reply.get('decision') == 'confirm':
            result = await asyncio.to_thread(self.store.confirm, state['owner'], state['request'],
                                             reply['revision'], now)
            if result['status'] == 'stale':
                return {'review': result, 'output': self._review(result), 'route': 'wait'}
            return {'output': self._result(result), 'question': None, 'route': 'end'}
        if reply.get('decision') == 'edit':
            if reply.get('edited_actions') is not None:
                return {'actions': reply['edited_actions'], 'received': reply['now'], 'edit': True,
                        'nl': False, 'mode': 'actions', 'route': 'prepare'}
            question = 'Describe the replacement actions for this review, or cancel.'
            return {'question': question, 'mode': 'edit', 'output': self._message(question),
                    'route': 'wait', 'edit': True, 'original': 'Current review: ' + str(state['review']['actions'])}
        answer = reply.get('answer')
        if not isinstance(answer, str) or not answer.strip():
            fail('invalid_answer', 'Answer the current question.')
        actions = copy.deepcopy(state['actions'])
        if state['mode'] == 'field':
            if state['field_name'] == 'amount_inr':
                parse_money(answer, allow_zero=actions[state['field_index']]['type'] == 'opening')
            actions[state['field_index']][state['field_name']] = answer.strip()
            return {'actions': actions, 'mode': 'actions', 'question': None, 'route': 'prepare'}
        if state['mode'] == 'select':
            if answer.strip() not in state['candidates']:
                fail('unknown_transaction', 'Select a transaction from the displayed owner-scoped candidates.')
            action = actions[state['selection_index']]
            action['transaction_id'], action['_selected'] = answer.strip(), True
            return {'actions': actions, 'mode': 'actions', 'question': None, 'route': 'prepare'}
        if state['mode'] == 'bucket':
            snapshot = await asyncio.to_thread(self.store.get_snapshot, state['owner'])
            name = next((n for n in snapshot['buckets'] if n.casefold() == answer.strip().casefold()), None)
            if name is None and answer.strip().casefold().startswith('create:'):
                name = answer.strip().split(':', 1)[1].strip()
                actions.insert(state['bucket_index'], {'type': 'create_bucket', 'name': name})
                actions[state['bucket_index'] + 1]['bucket_name'] = name
            elif name is None:
                fail('unknown_bucket', 'Select an existing bucket or explicitly reply create: <name>.')
            else:
                actions[state['bucket_index']]['bucket_name'] = name
            return {'actions': actions, 'mode': 'actions', 'question': None, 'route': 'prepare'}
        snapshot = await asyncio.to_thread(self.store.get_snapshot, state['owner'])
        output = await self._interpret(state, snapshot, answer)
        if output['kind'] == 'clarification':
            question = output['clarification_question'] or 'Please clarify the missing budgeting details.'
            return {'question': question, 'output': self._message(question), 'route': 'wait',
                    'original': state.get('original', '') + '\nOwner answer: ' + answer}
        if output['kind'] != 'mutation':
            fail('invalid_answer', 'Complete the current budgeting actions or cancel first.')
        return {'actions': output['actions'], 'mode': 'actions', 'nl': True, 'question': None,
                'received': reply['now'] if state.get('edit') else state['received'], 'route': 'prepare',
                'funding_resolved': True}
