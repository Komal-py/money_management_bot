"""Private-chat ingress: trusted Telegram identity before any budget operation."""
from datetime import datetime, timezone
from uuid import UUID


class BudgetController:
    def __init__(self, store, workflow, onboarding, reports, access, command_parser=None):
        self.store = store
        self.workflow = workflow
        self.onboarding = onboarding
        self.reports = reports
        self.access = access
        if command_parser is None:
            from budget_bot.telegram.commands import parse_command
            command_parser = parse_command
        self.command_parser = command_parser

    @staticmethod
    def _reply(chat_id, output):
        if isinstance(output, str):
            output = {'text': output}
        return [{'chat_id': chat_id, 'text': output['text'], 'keyboard': output.get('keyboard', [])}]

    async def handle(self, payload, now):
        callback = payload.get('callback_query') or {}
        message = payload.get('message') or callback.get('message')
        sender = callback.get('from') or (message or {}).get('from')
        if not message or not sender:
            return []
        chat = message.get('chat') or {}
        if chat.get('type') != 'private' or sender.get('is_bot') or sender.get('id') != chat.get('id'):
            return []
        telegram_id = sender['id']
        user = self.store.get_user(telegram_id)
        if not user or not user.get('active'):
            return self._reply(chat['id'], 'This bot is invite-only. Ask the administrator for an invite.')
        owner = user['owner_id']
        if callback:
            data = callback.get('data', '')
            if data.startswith('rev:'):
                try:
                    _, request, revision, decision = data.split(':')
                    UUID(request)
                    revision = int(revision)
                    if revision < 1 or decision not in {'confirm', 'edit', 'cancel'}:
                        raise ValueError
                except (ValueError, TypeError):
                    return self._reply(chat['id'], 'That review button is invalid. Open your current review.')
                return self._reply(chat['id'], await self.workflow.decide(owner, request, revision, decision, now))
            return self._reply(chat['id'], 'That button is not available. Use /help.')
        text = message.get('text', '')
        received = datetime.fromtimestamp(message.get('date', int(now.timestamp())), timezone.utc)
        if text.startswith('/') and self.command_parser:
            from budget_bot.telegram.commands import CommandError, HELP_TEXT
            try:
                command = self.command_parser(text, received, user['timezone'])
            except CommandError as error:
                return self._reply(chat['id'], error.message + '\nSee /help for syntax.')
            if command['kind'] == 'help':
                return self._reply(chat['id'], HELP_TEXT)
            if command['kind'] == 'mutation':
                return self._reply(chat['id'], await self.workflow.submit(owner, command['actions'], received))
            if command['kind'] == 'query':
                query = command['query']
                if query['report'] == 'balances':
                    return self._reply(chat['id'], self.reports.balances(owner))
                if query['report'] == 'spending':
                    return self._reply(chat['id'], self.reports.spending(
                        owner, query.get('period', 'month'), received,
                        start=query.get('start'), end=query.get('end'), bucket_name=query.get('bucket_name')))
        return self._reply(chat['id'], 'Use /help for commands. All money changes require review and confirmation.')
