"""Guided, step-by-step Add expense / Add income drafts.

The flow only collects fields. It never writes money: the finished draft is
handed to ``workflow.submit`` which produces the normal Confirm/Edit/Cancel
review. Drafts are process-local and short-lived (a restart simply drops an
unfinished draft; the user starts again with /expense or /income).

Callback protocol (all <= 64 bytes, owner-bound by the controller):
  entry:expense | entry:income      start a guided draft
  entry:bucket:<index>              choose a bucket shown on the bucket step
  entry:date:today | entry:date:yesterday
  entry:back | entry:cancel
"""
from datetime import date, timedelta
import re

from budget_bot.domain.errors import BudgetError
from budget_bot.domain.money import format_money, parse_money

DRAFT_TTL = timedelta(minutes=30)
STEPS = {'expense': ('amount', 'bucket', 'description', 'date'),
         'income': ('amount', 'description', 'date')}
_BUCKETS_SHOWN = 24


class GuidedEntry:
    def __init__(self, store, workflow):
        self.store = store
        self.workflow = workflow
        self._drafts = {}

    # -- state -------------------------------------------------------------
    def active(self, owner_id, now):
        draft = self._drafts.get(owner_id)
        if draft and now - draft['updated'] > DRAFT_TTL:
            self._drafts.pop(owner_id, None)
            return False
        return draft is not None

    def cancel(self, owner_id):
        return self._drafts.pop(owner_id, None) is not None

    # -- rendering -----------------------------------------------------------
    @staticmethod
    def _controls(back=True):
        row = [{'text': 'Back', 'data': 'entry:back'}] if back else []
        return [row + [{'text': 'Cancel', 'data': 'entry:cancel'}]]

    def _prompt(self, owner_id, draft):
        kind, step = draft['kind'], draft['step']
        title = 'Add expense' if kind == 'expense' else 'Add income'
        first = STEPS[kind][0] == step
        if step == 'amount':
            text = f'{title}: how much INR? (e.g. 250 or 1,200.50)'
            return {'text': text, 'keyboard': self._controls(back=not first)}
        if step == 'bucket':
            names = sorted(self.store.get_snapshot(owner_id)['buckets'], key=str.casefold)[:_BUCKETS_SHOWN]
            draft['bucket_choices'] = names
            rows = [[{'text': name, 'data': f'entry:bucket:{i}'} for i, name in enumerate(names)][j:j + 2]
                    for j in range(0, len(names), 2)]
            text = (f'{title} of {format_money(draft["paise"])}: which bucket? '
                    'Tap one or type its name.')
            return {'text': text, 'keyboard': rows + self._controls()}
        if step == 'description':
            return {'text': f'{title}: short description? (e.g. Groceries)', 'keyboard': self._controls()}
        return {'text': f'{title}: which date? Tap a button or type YYYY-MM-DD.',
                'keyboard': [[{'text': 'Today', 'data': 'entry:date:today'},
                              {'text': 'Yesterday', 'data': 'entry:date:yesterday'}]] + self._controls()}

    # -- transitions ---------------------------------------------------------
    def start(self, owner_id, kind, now):
        if kind not in STEPS:
            raise BudgetError('invalid_entry', 'Choose Add expense or Add income.')
        if kind == 'expense' and not self.store.get_snapshot(owner_id)['buckets']:
            self._drafts.pop(owner_id, None)
            return {'text': 'Create a bucket first with /bucket name, then add an expense.', 'keyboard': []}
        draft = {'kind': kind, 'step': 'amount', 'updated': now}
        self._drafts[owner_id] = draft
        return self._prompt(owner_id, draft)

    def _advance(self, owner_id, draft, now):
        steps = STEPS[draft['kind']]
        draft['step'] = steps[steps.index(draft['step']) + 1]
        draft['updated'] = now
        return self._prompt(owner_id, draft)

    def back(self, owner_id, now):
        draft = self._drafts.get(owner_id)
        steps = STEPS[draft['kind']]
        index = steps.index(draft['step'])
        if index:
            draft['step'] = steps[index - 1]
        draft['updated'] = now
        return self._prompt(owner_id, draft)

    async def _finish(self, owner_id, draft, expression, received_at, operation):
        action = {'type': draft['kind'], 'amount_inr': draft['amount_inr'],
                  'description': draft['description'], 'date_expression': expression}
        if draft['kind'] == 'expense':
            action['bucket_name'] = draft['bucket_name']
        # Drop the draft only after the review exists, so a pending-review error
        # leaves the answers in place for a retry after /cancel of the other review.
        output = await self.workflow.submit(owner_id, [action], received_at, request_id=operation)
        self._drafts.pop(owner_id, None)
        return output

    async def handle_text(self, owner_id, text, received_at, now, operation=None):
        draft = self._drafts[owner_id]
        value = text.strip()
        step = draft['step']
        if step == 'amount':
            try:
                paise = parse_money(value)
            except BudgetError:
                return {'text': 'Please send a positive INR amount with at most two decimals, e.g. 250.',
                        'keyboard': self._controls(back=False)}
            draft['paise'] = paise
            draft['amount_inr'] = f'{paise // 100}.{paise % 100:02d}'
            return self._advance(owner_id, draft, now)
        if step == 'bucket':
            names = self.store.get_snapshot(owner_id)['buckets']
            match = next((n for n in names if n.casefold() == value.casefold()), None)
            if match is None:
                prompt = self._prompt(owner_id, draft)
                return {**prompt, 'text': 'No bucket by that name. ' + prompt['text']}
            draft['bucket_name'] = match
            return self._advance(owner_id, draft, now)
        if step == 'description':
            if not value or len(value) > 240:
                return {'text': 'Description must contain 1–240 characters.', 'keyboard': self._controls()}
            draft['description'] = value
            return self._advance(owner_id, draft, now)
        lowered = value.lower()
        if lowered not in ('today', 'yesterday'):
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                prompt = self._prompt(owner_id, draft)
                return {**prompt, 'text': 'Date must be today, yesterday or YYYY-MM-DD. ' + prompt['text']}
            lowered = value
        return await self._finish(owner_id, draft, lowered, received_at, operation)

    async def handle_callback(self, owner_id, data, received_at, now, operation=None):
        parts = data.split(':')
        if len(parts) == 2 and parts[1] in STEPS:
            return self.start(owner_id, parts[1], now)
        if not self.active(owner_id, now):
            return {'text': 'That entry is no longer active. Use /expense or /income to start again.',
                    'keyboard': []}
        draft = self._drafts[owner_id]
        if parts == ['entry', 'cancel']:
            self.cancel(owner_id)
            return {'text': 'Entry cancelled. Nothing was saved.', 'keyboard': []}
        if parts == ['entry', 'back']:
            return self.back(owner_id, now)
        if len(parts) == 3 and parts[1] == 'bucket' and draft['step'] == 'bucket':
            choices = draft.get('bucket_choices') or []
            if re.fullmatch(r'[0-9]{1,2}', parts[2]) and int(parts[2]) < len(choices):
                return await self.handle_text(owner_id, choices[int(parts[2])], received_at, now, operation)
        if len(parts) == 3 and parts[1] == 'date' and draft['step'] == 'date' and parts[2] in ('today', 'yesterday'):
            return await self._finish(owner_id, draft, parts[2], received_at, operation)
        return {'text': 'That button is not current. ' + self._prompt(owner_id, draft)['text'],
                'keyboard': self._prompt(owner_id, draft)['keyboard']}
