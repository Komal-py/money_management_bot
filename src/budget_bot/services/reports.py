"""Deterministic read-only views of owner-scoped store facts.

Money is formatted using integer operations while W1's external money module is
not yet present. No inferred ledger or target-history calculations live here.
"""
import calendar
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo


def _money(paise):
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
             f'Total spent: {_money(result["total"])}', f'{result["count"]} expenses']
    for bucket in sorted(result['by_bucket'], key=lambda name: (name.casefold(), name)):
        lines.append(f'{bucket}: {_money(result["by_bucket"][bucket])}')
    if not result['count']:
        lines.append('No active expenses in this period.')
    return '\n'.join(lines)


class ReportService:
    def __init__(self, store):
        self.store = store

    def balances(self, owner_id):
        snapshot = self.store.get_snapshot(owner_id)
        lines = ['Virtual INR balances', f'Unallocated pool: {_money(snapshot["pool"])}']
        total = snapshot['pool']
        for name in sorted(snapshot['buckets'], key=lambda name: (name.casefold(), name)):
            bucket = snapshot['buckets'][name]
            total += bucket['balance']
            lines.append(f'{name}: {_money(bucket["balance"])}')
            if bucket['target'] is not None:
                lines.append(f'  Monthly target: {_money(bucket["target"])}')
            if bucket['balance'] < 0:
                lines.append(f'Warning: {name} has a negative balance.')
        lines.append(f'Total available: {_money(total)}')
        return '\n'.join(lines)

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
        # Snapshot targets are current settings, not historical target facts.
        # Only compare a receipt-anchored current full month; never retrofit a
        # current target onto historical days or ranges.
        if period == 'month':
            for name in sorted(result['by_bucket'], key=lambda name: (name.casefold(), name)):
                target = snapshot['buckets'][name]['target']
                amount = result['by_bucket'][name]
                if target is not None and amount >= target:
                    text += (f'\nWarning: {name} monthly spending {_money(amount)} '
                             f'reached or exceeded target {_money(target)}.')
        return text

    def day(self, owner_id, date_string):
        day = _date(date_string)
        return _summary(self.store.spending(owner_id, day, day))
