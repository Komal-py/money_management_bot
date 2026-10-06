"""Private-chat ingress: trusted Telegram identity before any budget operation."""


class BudgetController:
    def __init__(self, store, workflow, onboarding, reports, access):
        self.store = store
        self.workflow = workflow
        self.onboarding = onboarding
        self.reports = reports
        self.access = access

    async def handle(self, payload, now):
        message = payload.get('message') or (payload.get('callback_query') or {}).get('message')
        sender = (payload.get('callback_query') or {}).get('from') or (message or {}).get('from')
        if not message or not sender:
            return []
        chat = message.get('chat') or {}
        if chat.get('type') != 'private' or sender.get('is_bot') or sender.get('id') != chat.get('id'):
            return []
        telegram_id = sender['id']
        user = self.store.get_user(telegram_id)
        if not user or not user.get('active'):
            return [{'chat_id': chat['id'], 'text': 'This bot is invite-only. Ask the administrator for an invite.',
                     'keyboard': []}]
        return [{'chat_id': chat['id'], 'text': 'Budget access verified. Financial features are being integrated.',
                 'keyboard': []}]
