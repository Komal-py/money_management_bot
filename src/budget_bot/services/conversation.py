"""Conversation dispatch for a caller-authorized owner; never confirms money."""
from datetime import datetime

from budget_bot.ai.schemas import Query
from budget_bot.domain.dates import local_today
from budget_bot.domain.errors import BudgetError
from budget_bot.telegram.calendar import day_view, month_view, parse_callback


class ConversationRouter:
    def __init__(self, store, workflow, reports):
        self.store = store
        self.workflow = workflow
        self.reports = reports

    async def dispatch(self, owner_id, text, received_at, now, *, query=None, callback_data=None):
        if callback_data is not None:
            return self._calendar(owner_id, callback_data)
        if query is not None:
            return self._report(owner_id, query, received_at)
        try:
            output = await self.workflow.answer(owner_id, text if text is not None else '', now)
        except BudgetError as error:
            if error.code != 'no_question':
                raise
            if text is None or not text.strip():
                return self.menu()
            # Store reviews also survive checkpoint loss and can originate in
            # onboarding. Never bypass one by starting an unrelated NL request.
            pending = self.store.get_pending(owner_id)
            if pending and datetime.fromisoformat(pending['expires_at']) > now:
                raise BudgetError('pending_review', 'Finish or cancel the current interaction first.')
            output = await self.workflow.natural_language(owner_id, text, received_at)
        if output.get('query') is not None:
            return self._report(owner_id, output['query'], received_at)
        return output

    def menu(self) -> dict:
        # Commands are already supported by ingress. Do not invent menu callback
        # protocols or guided financial-entry buttons that ingress cannot handle.
        return {'text': 'Your virtual INR budget\n'
                        'Tell me an expense or money change, or use these commands:\n'
                        '/balance\n/spending today\n/spending week\n/spending month\n/calendar\n'
                        '/help\n/cancel\n'
                        'Every money change needs your review and explicit Confirm. '
                        'Reports and calendar navigation never change money.', 'keyboard': []}

    def _calendar(self, owner_id, payload):
        if not isinstance(payload, str) or not payload.startswith(('cal:', 'day:')):
            return None
        try:
            selection = parse_callback(payload)
            if selection['kind'] == 'month':
                return month_view(selection['year'], selection['month'])
            return day_view(self.store, owner_id, selection['date'], page=selection['page'])
        except BudgetError:
            raise
        except ValueError:
            return {'text': 'That calendar date or page is invalid. Open /calendar again.', 'keyboard': []}

    def _report(self, owner_id, query, received_at):
        try:
            return self._render_report(owner_id, query, received_at)
        except BudgetError as error:
            if error.code != 'invalid_date':
                raise
        except ValueError:
            pass
        return {'text': 'Choose a valid report, date range, and existing bucket. '
                        'Bucket filters and date ranges apply to spending only. See /help.', 'keyboard': []}

    def _render_report(self, owner_id, query, received_at):
        if not isinstance(query, dict) or set(query) != {'report', 'period', 'start', 'end', 'bucket_name'}:
            raise ValueError('Invalid query envelope.')
        query = Query.model_validate(query).model_dump()
        if query['report'] != 'spending' and (query['bucket_name'] is not None or query['period'] == 'range'):
            raise ValueError('This view does not support filters.')
        snapshot = self.store.get_snapshot(owner_id)
        today = local_today(received_at, snapshot['timezone'])
        if query['bucket_name'] is not None:
            name = query['bucket_name']
            canonical = next((n for n in snapshot['buckets'] if n.casefold() == name.casefold()), None)
            if canonical is None:
                raise ValueError('Unknown bucket.')
            query['bucket_name'] = canonical
        if query['report'] == 'balances':
            output = {'text': self.reports.balances(owner_id), 'keyboard': []}
        elif query['report'] == 'spending':
            output = {'text': self.reports.spending(
                owner_id, query['period'], received_at, start=query['start'],
                end=query['end'], bucket_name=query['bucket_name']), 'keyboard': []}
        else:
            output = month_view(today.year, today.month)
        return {**output, 'query': query}
