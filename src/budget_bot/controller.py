"""Private-chat ingress: trusted Telegram identity before any budget operation."""
from datetime import datetime, timezone
import re
from uuid import UUID

from budget_bot.domain.errors import BudgetError


class BudgetController:
    def __init__(self, store, workflow, onboarding, reports, access, command_parser=None, *, conversation=None):
        self.store = store
        self.workflow = workflow
        self.onboarding = onboarding
        self.reports = reports
        self.access = access
        self.conversation = conversation
        if command_parser is None:
            from budget_bot.telegram.commands import parse_command
            command_parser = parse_command
        self.command_parser = command_parser

    def _conversation(self):
        if self.conversation is None:
            from budget_bot.services.conversation import ConversationRouter
            self.conversation = ConversationRouter(self.store, self.workflow, self.reports)
        return self.conversation

    @staticmethod
    def _reply(chat_id, output):
        if isinstance(output, str):
            output = {'text': output}
        return [{'chat_id': chat_id, 'text': output['text'], 'keyboard': output.get('keyboard', [])}]

    def _setup_output(self, output):
        review = output.get('review')
        if review:
            from budget_bot.ai.rendering import render_review
            output = {**output, 'text': render_review(review), 'keyboard': [[
                {'text': decision.title(),
                 'data': f'rev:{review["request_id"]}:{review["revision"]}:{decision}'}
                for decision in ('confirm', 'edit', 'cancel')]]}
        return output

    def _setup_edit(self, owner, request, revision, now):
        # Use the service's existing draft lock: checking then calling handle()
        # separately would let an old button reset a newly created draft.
        with self.store.engine.begin() as connection:
            self.onboarding._lock(connection, owner)
            state = self.onboarding._load(connection, owner)
            pending = self.store.get_pending(owner)
            if (not state or state['step'] != 'review' or state['request_id'] != request
                    or not pending or pending['owner_id'] != owner
                    or pending['request_id'] != request or pending['revision'] != revision
                    or datetime.fromisoformat(pending['expires_at']) <= now):
                raise BudgetError('stale_review', 'That review is no longer current. Open your current review.')
            if self.onboarding._cancel_review(owner, state, now):
                return self._conversation().menu()
            state = self.onboarding._new_state()
            self.onboarding._save(connection, owner, state, now)
            return self.onboarding._prompt(state)

    def _setup_cancelled(self, owner, request, now):
        # A replayed cancellation result must never cancel a newer draft.
        with self.store.engine.begin() as connection:
            self.onboarding._lock(connection, owner)
            state = self.onboarding._load(connection, owner)
            if state and state['step'] == 'review' and state['request_id'] == request:
                state = self.onboarding._new_state()
                state['step'] = 'cancelled'
                self.onboarding._save(connection, owner, state, now)

    async def handle(self, payload, now):
        callback = payload.get('callback_query') or {}
        message = callback.get('message') if callback else payload.get('message')
        sender = callback.get('from') if callback else (message or {}).get('from')
        if not message or not sender:
            return []
        chat = message.get('chat') or {}
        telegram_id = sender.get('id')
        chat_id = chat.get('id')
        if (chat.get('type') != 'private' or sender.get('is_bot')
                or type(telegram_id) is not int or not 0 < telegram_id < 2**63
                or type(chat_id) is not int or telegram_id != chat_id):
            return []
        user = self.store.get_user(telegram_id)
        if user and not user.get('active'):
            return self._reply(chat_id, 'Your access has been revoked. Contact the administrator.')
        if not user:
            text = message.get('text', '')
            invite = re.fullmatch(r'/start ([A-Za-z0-9_-]+)', text) if isinstance(text, str) else None
            if not callback and invite:
                try:
                    owner = self.store.redeem_invite(invite.group(1), telegram_id, now)
                    return self._reply(chat_id, self.onboarding.handle(owner, '/start', now))
                except BudgetError as error:
                    return self._reply(chat_id, error.message)
            return self._reply(chat['id'], 'This bot is invite-only. Ask the administrator for an invite.')
        try:
            return self._reply(chat_id, await self._dispatch(user, message, callback, now))
        except BudgetError as error:
            return self._reply(chat_id, error.message)

    async def _dispatch(self, user, message, callback, now):
        from budget_bot.telegram.commands import CommandError, HELP_TEXT

        owner = user['owner_id']
        received = now
        if callback:
            data = callback.get('data', '')
            if not isinstance(data, str):
                return 'That button is not available. Use /help.'
            if data.startswith('rev:'):
                try:
                    _, request, revision, decision = data.split(':')
                    if str(UUID(request)) != request or not re.fullmatch(r'[1-9][0-9]*', revision):
                        raise ValueError
                    revision = int(revision)
                    if revision < 1 or decision not in {'confirm', 'edit', 'cancel'}:
                        raise ValueError
                except (ValueError, TypeError):
                    return 'That review button is invalid. Open your current review.'
                setup = self.onboarding is not None and self.onboarding.active(owner)
                if setup and decision == 'edit':
                    async with self.workflow._locked(owner):
                        return self._setup_edit(owner, request, revision, now)
                output = await self.workflow.decide(owner, request, revision, decision, now)
                if setup and decision == 'cancel' and (output.get('result') or {}).get('status') == 'cancelled':
                    self._setup_cancelled(owner, request, now)
                return output
            if data in {'setup:back', 'setup:cancel', 'setup:finish', 'setup:more'}:
                if user.get('onboarded'):
                    return 'Setup is already complete. Use /start for the menu.'
                return self._setup_output(self.onboarding.handle(owner, data, now))
            if data.startswith(('cal:', 'day:')):
                if not user.get('onboarded'):
                    return self._setup_output(self.onboarding.handle(owner, '/start', now))
                output = await self._conversation().dispatch(owner, '', received, now, callback_data=data)
                return output or 'That button is not available. Use /help.'
            return 'That button is not available. Use /help.'
        text = message.get('text', '')
        if not isinstance(text, str) or not text:
            return 'Send a text message or use /help.'
        receipt = message.get('date', int(now.timestamp()))
        try:
            if type(receipt) is not int:
                raise ValueError
            received = datetime.fromtimestamp(receipt, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return 'That message timestamp is invalid. Please send it again.'
        if text.startswith('/') and self.command_parser:
            try:
                command = self.command_parser(text, received, user['timezone'])
            except CommandError as error:
                return error.message + '\nSee /help for syntax.'
            if command['kind'] == 'help':
                return HELP_TEXT
            if command['kind'] == 'admin':
                return self.access.handle_admin(owner, command['command'], command['args'], now)
            if command['kind'] == 'timezone':
                self.store.set_timezone(owner, command['args'][0])
                return f'Timezone set to {command["args"][0]}.'
            if command['kind'] == 'setup':
                if user.get('onboarded'):
                    return self._conversation().menu()
                return self._setup_output(self.onboarding.handle(owner, '/start', now))
            if command['kind'] == 'cancel':
                if self.onboarding is not None and self.onboarding.active(owner):
                    return self.onboarding.cancel(owner, now)
                pending = self.store.get_pending(owner)
                if pending:
                    return await self.workflow.decide(owner, pending['request_id'], pending['revision'], 'cancel', now)
                try:
                    return await self.workflow.answer(owner, '/cancel', now)
                except BudgetError as error:
                    if error.code != 'no_question':
                        raise
                return 'No pending review to cancel.'
            if self.onboarding is not None and not user.get('onboarded'):
                return self._setup_output(self.onboarding.handle(owner, '/start', now))
            if command['kind'] == 'mutation':
                return await self.workflow.submit(owner, command['actions'], received)
            if command['kind'] == 'query':
                return await self._conversation().dispatch(owner, text, received, now, query=command['query'])
        if self.onboarding is not None and not user.get('onboarded'):
            return self._setup_output(self.onboarding.handle(owner, text, now))
        return await self._conversation().dispatch(owner, text, received, now)
