"""Deterministic English views of backend-validated facts; never calls AI."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from budget_bot.domain.money import format_money, parse_money
from budget_bot.telegram.tables import table

_LABELS = {'opening': 'Opening money', 'income': 'Income', 'allocate': 'Allocation',
           'transfer': 'Transfer', 'expense': 'Expense', 'create_bucket': 'Create bucket',
           'set_target': 'Monthly target', 'undo': 'Undo', 'correct': 'Correction'}
_UUID = re.compile(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b')


def _money(paise):
    return 'INR ' + format_money(paise)[1:]


def _line(value):
    """Keep user labels from forging extra review lines; hide authority IDs."""
    return _UUID.sub('[record]', ' '.join(value.split()))


def _balances(snapshot, projected=False):
    lines = ['Projected balances (after confirmation):' if projected else 'Balances:',
             f"Timezone: {_line(snapshot['timezone'])}"]
    rows = [['Pool:', _money(snapshot['pool'])]]
    for name, bucket in sorted(snapshot['buckets'].items(), key=lambda pair: (pair[0].casefold(), pair[0])):
        rows.append([f'{_line(name)}:', _money(bucket['balance'])])
        if bucket.get('target') is not None:
            rows.append(['  Monthly target:', _money(bucket['target'])])
    total = snapshot['pool'] + sum(bucket['balance'] for bucket in snapshot['buckets'].values())
    lines.append(table(rows, footer=[['Total virtual money:', _money(total)]]))
    return lines


def render_balances(snapshot: dict) -> str:
    return '\n'.join(_balances(snapshot))


def _direction(kind, bucket=None, source=None, destination=None, reverse=False):
    origin, target = {
        'opening': ('Opening money', 'Pool'), 'income': ('New income', 'Pool'),
        'allocate': ('Pool', bucket), 'transfer': (source, destination),
        'expense': (bucket, 'Spending'),
    }[kind]
    if reverse:
        origin, target = target, origin
    return f'Direction: {_line(origin)} → {_line(target)}'


def _transaction(tx, reverse=False):
    lines = [f"{_LABELS[tx['type']]}; Amount: {_money(tx['amount'])}",
             _direction(tx['type'], tx.get('bucket'), tx.get('source'), tx.get('destination'), reverse),
             f"Date: {tx['date']}"]
    if tx.get('bucket'):
        lines.append(f"Bucket: {_line(tx['bucket'])}")
    if tx.get('description'):
        lines.append(f"Description: {_line(tx['description'])}")
    return lines


def _action(action, plan):
    kind = action['type']
    lines = [_LABELS[kind]]
    if kind in {'correct', 'undo'}:
        events = [event for event in plan['events'] if event.get('previous') and (
            event['transaction']['id'] == action.get('transaction_id')
            or event['transaction']['batch_id'] == action.get('batch_id'))]
        for event in events:
            if kind == 'correct':
                lines.extend(['Before:', *_transaction(event['previous']),
                              'After:', *_transaction(event['transaction'])])
            else:
                lines.extend(_transaction(event['previous'], reverse=True))
        return lines
    if 'amount_inr' in action:
        lines.append('Amount: ' + _money(parse_money(action['amount_inr'], allow_zero=True)))
    if kind in {'opening', 'income', 'allocate', 'transfer', 'expense'}:
        lines.append(_direction(kind, action.get('bucket_name'), action.get('source_bucket'),
                                action.get('destination_bucket')))
    for field, label in [('bucket_name', 'Bucket'), ('name', 'Bucket'), ('source_bucket', 'Source bucket'),
                         ('destination_bucket', 'Destination bucket'), ('description', 'Description'),
                         ('date_expression', 'Date')]:
        if action.get(field) is not None:
            lines.append(f'{label}: {_line(action[field])}')
    if kind == 'set_target' and action.get('remove'):
        lines.append('Monthly target: off')
    return lines


def render_review(review: dict) -> str:
    plan = review['plan']
    lines = ['Review — no financial changes recorded.']
    if review.get('status') == 'stale':
        lines.append('Balances changed. Review the updated proposal before confirming again.')
    lines.append('All listed actions apply together or none do.')
    for index, action in enumerate(plan['actions'], start=1):
        action_lines = _action(action, plan)
        lines.append(f'{index}. {action_lines[0]}')
        lines.extend('  ' + line for line in action_lines[1:])
    lines.extend(_balances(plan['snapshot'], projected=True))
    lines.extend('Warning: ' + _line(warning) for warning in plan['warnings'])
    if review.get('expires_at'):
        expires = datetime.fromisoformat(review['expires_at'])
        local = expires.astimezone(ZoneInfo(plan['snapshot']['timezone']))
        lines.append(f"Review expires: {local:%Y-%m-%d %H:%M} ({plan['snapshot']['timezone']})")
    lines.append('Confirm / Edit / Cancel')
    return '\n'.join(lines)


def render_result(result: dict) -> str:
    status = result['status']
    if status == 'stale':
        return render_review(result)
    if status in {'cancelled', 'canceled'}:
        return 'Cancelled. No financial changes recorded.'
    if status == 'expired':
        return 'This review expired. Submit a new request; no financial changes recorded.'
    if status != 'committed':
        return 'No committed result is available. Check the current review before proceeding.'
    lines = ['Recorded.']
    lines.extend(_line(summary) for summary in result.get('summary', []))
    # Undo/correction events retain their original transaction batch. The frozen
    # result has no events; only backend-generated summary selectors identify
    # those revised records. Never search IDs in arbitrary user descriptions.
    revised_ids = set()
    for summary in result.get('summary', []):
        match = re.match(r'^(?:Correct |Undo (?:opening|income|allocate|transfer|expense) )([^:]+):', summary)
        if match and _UUID.fullmatch(match[1]):
            revised_ids.add(match[1])
    if result.get('batch_id'):
        for transaction in result['snapshot']['transactions']:
            if transaction['batch_id'] == result['batch_id'] or transaction['id'] in revised_ids:
                lines.extend(_transaction(transaction, reverse=not transaction['active']))
    lines.extend(_balances(result['snapshot']))
    lines.extend('Warning: ' + _line(warning) for warning in result.get('warnings', []))
    return '\n'.join(lines)
