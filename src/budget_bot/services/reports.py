"""Deterministic read-only views of owner-scoped store facts."""
import calendar
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from budget_bot.telegram.tables import table


def _money(paise):
    # Domain formatting puts the minus after ₹; retain this view's established
    # -₹ spelling and strict integer-only boundary without float conversion.
    if isinstance(paise, bool) or not isinstance(paise, int):
        raise ValueError('Money must be integer paise.')
    whole, fraction = divmod(abs(paise), 100)
    return f'{"-" if paise < 0 else ""}₹{whole}.{fraction:02d}'


def _date(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('Use a valid YYYY-MM-DD date.')
    return date.fromisoformat(value)


def _bucket(snapshot, name):
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError('Unknown bucket.')
    for canonical in snapshot['buckets']:
        if canonical.casefold() == name.strip().casefold():
            return canonical
    raise ValueError('Unknown bucket.')


def _summary(result):
    lines = [f'Spending: {result["start"]} to {result["end"]}',
             f'{result["count"]} expenses']
    rows = [[f'{bucket}:', _money(result['by_bucket'][bucket])]
            for bucket in sorted(result['by_bucket'], key=lambda name: (name.casefold(), name))]
    lines.append(table(rows, footer=[['Total spent:', _money(result['total'])]]))
    if not result['count']:
        lines.append('No active expenses in this period.')
    return '\n'.join(lines)


class ReportService:
    def __init__(self, store):
        self.store = store

    def balances(self, owner_id):
        snapshot = self.store.get_snapshot(owner_id)
        rows = [['Unallocated pool:', _money(snapshot['pool'])]]
        warnings = []
        total = snapshot['pool']
        for name in sorted(snapshot['buckets'], key=lambda name: (name.casefold(), name)):
            bucket = snapshot['buckets'][name]
            total += bucket['balance']
            rows.append([f'{name}:', _money(bucket['balance'])])
            if bucket['target'] is not None:
                rows.append(['  Monthly target:', _money(bucket['target'])])
            if bucket['balance'] < 0:
                warnings.append(f'Warning: {name} has a negative balance.')
        lines = ['Virtual INR balances', table(rows, footer=[['Total available:', _money(total)]])]
        return '\n'.join(lines + warnings)

    def spending(self, owner_id, period, received_at, start=None, end=None, bucket_name=None):
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError('Receipt timestamp must be timezone-aware.')
        snapshot = self.store.get_snapshot(owner_id)
        today = received_at.astimezone(ZoneInfo(snapshot['timezone'])).date()
        if period == 'today':
            first = last = today
        elif period == 'week':
            first = today - timedelta(days=today.weekday())
            last = first + timedelta(days=6)
        elif period == 'month':
            first = today.replace(day=1)
            last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        elif period == 'range':
            first, last = _date(start), _date(end)
            if first > last:
                raise ValueError('Start date must not follow end date.')
        else:
            raise ValueError('Choose today, week, month, or range.')
        bucket = _bucket(snapshot, bucket_name)
        result = self.store.spending(owner_id, first, last, bucket)
        text = _summary(result)
        # Current bucket settings are not historical facts. A missing prior
        # version means no target; an explicit removal must stop carry-forward.
        if period == 'month':
            for name in sorted(result['by_bucket'], key=lambda name: (name.casefold(), name)):
                versions = [v for v in snapshot['targets']
                            if v['bucket_name'] == name and v['effective_month'] <= first.isoformat()]
                version = max(versions, key=lambda v: (v['effective_month'], v['revision']), default=None)
                target = version['amount'] if version is not None else None
                amount = result['by_bucket'][name]
                if target is not None and amount >= target:
                    text += (f'\nWarning: {name} monthly spending {_money(amount)} '
                             f'reached or exceeded target {_money(target)}.')
        return text

    def day(self, owner_id, date_string):
        day = _date(date_string)
        return _summary(self.store.spending(owner_id, day, day))
