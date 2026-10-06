"""Conversation dispatch for a caller-authorized owner; never confirms money."""
from datetime import datetime

from pydantic import ValidationError

from budget_bot.ai.schemas import Query
from budget_bot.domain.dates import local_today
from budget_bot.domain.errors import BudgetError
from budget_bot.telegram.calendar import CalendarInputError, day_view, month_view, parse_callback


class _ReportInputError(ValueError):
    """Invalid caller report input, not a dependency failure."""


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
            query_received_at = datetime.fromisoformat(output.get('query_received_at', received_at.isoformat()))
            return self._report(owner_id, output['query'], query_received_at)
        return output

    def menu(self) -> dict:
        # The persistent reply keyboard sends plain text labels that ingress maps
        # to these same existing commands; it never adds callback protocols.
        from budget_bot.telegram.menu import main_menu_keyboard
        return {'text': 'Your virtual INR budget\n'
                        'Use the menu buttons below, type an expense in plain words '
                        '(e.g. "Spent 120 on Food"), or type / for all commands.\n'
                        'Every money change needs your review and explicit Confirm. '
                        'Reports and calendar navigation never change money.',
                'keyboard': main_menu_keyboard()}

    def _calendar(self, owner_id, payload):
        if not isinstance(payload, str) or not payload.startswith(('cal:', 'day:')):
            return None
        try:
            try:
                selection = parse_callback(payload)
            except ValueError as error:
                raise CalendarInputError(str(error)) from None
            if selection['kind'] == 'month':
                return month_view(selection['year'], selection['month'])
            return day_view(self.store, owner_id, selection['date'], page=selection['page'])
        except CalendarInputError:
            return {'text': 'That calendar date or page is invalid. Open /calendar again.', 'keyboard': []}

    def _report(self, owner_id, query, received_at):
        try:
            return self._render_report(owner_id, query, received_at)
        except _ReportInputError:
            pass
        return {'text': 'Choose a valid report, date range, and existing bucket. '
                        'Bucket filters and date ranges apply to spending only. See /help.', 'keyboard': []}

    def _render_report(self, owner_id, query, received_at):
        if not isinstance(query, dict) or set(query) != {'report', 'period', 'start', 'end', 'bucket_name'}:
            raise _ReportInputError('Invalid query envelope.')
        try:
            query = Query.model_validate(query).model_dump()
        except ValidationError:
            raise _ReportInputError('Invalid query fields.') from None
        if query['report'] != 'spending' and (query['bucket_name'] is not None or query['period'] == 'range'):
            raise _ReportInputError('This view does not support filters.')
        snapshot = self.store.get_snapshot(owner_id)
        try:
            today = local_today(received_at, snapshot['timezone'])
        except BudgetError as error:
            if error.code != 'invalid_date':
                raise
            raise _ReportInputError(error.message) from None
        if query['bucket_name'] is not None:
            name = query['bucket_name']
            canonical = next((n for n in snapshot['buckets'] if n.casefold() == name.casefold()), None)
            if canonical is None:
                raise _ReportInputError('Unknown bucket.')
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
