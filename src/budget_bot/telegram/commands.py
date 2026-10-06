"""Bounded deterministic command proposals; never performs financial writes."""
import re
import shlex
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HELP_TEXT = """Commands (quote names/descriptions containing spaces):
/income amount description [date]
/bucket name
/allocate amount bucket
/expense amount bucket description [date]
/transfer amount source destination
/undo [transaction-id|last]
/correct transaction-id amount=... bucket=... date=... description=...
/target bucket amount|off
/balance
/spending today|week|month [bucket], or /spending start end [bucket]
/calendar
/start [invite]
/invite
/users
/revoke telegram-id
/timezone zone
/cancel
/help
Money changes are proposals and require review and confirmation.
"""


class CommandError(ValueError):
    """Safe syntax error, including recoverable required fields."""

    def __init__(self, message, *, code="invalid_command", missing_fields=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.missing_fields = missing_fields or []


def _missing(fields):
    raise CommandError("Please supply: " + ", ".join(fields), code="missing_fields", missing_fields=fields)


def _money(value):
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value) or not any(c in '123456789' for c in value):
        raise CommandError("Amount must be a positive INR decimal with at most two decimal places.")
    return value


def _bounded(value, maximum, label):
    if not value.strip() or len(value) > maximum:
        raise CommandError(f"{label} must contain 1–{maximum} characters.")
    return value.strip()


def _date(value):
    if value in ("today", "yesterday"):
        return value
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise CommandError("Date must be today, yesterday or YYYY-MM-DD.") from None
    return value


def parse_command(text, received_at, timezone):
    """Return the frozen six-key command envelope, or a safe CommandError.

    Missing financial fields raise code=missing_fields with missing_fields so
    the parent can request an answer rather than inventing a description.
    Date resolution and owner-scoped references remain domain/controller work.
    """
    if not isinstance(text, str) or len(text) > 4096:
        raise CommandError("Command exceeds Telegram text limits.")
    try:
        words = shlex.split(text)
    except ValueError:
        raise CommandError("Unclosed quote in command.") from None
    if not words or not re.fullmatch(r"/[A-Za-z]+(?:@[A-Za-z0-9_]+)?", words[0]):
        raise CommandError("Use /help for supported commands.")
    command = words[0][1:].split('@')[0].lower()
    args = words[1:]
    result = dict(kind="mutation", actions=[], query=None, command=command, args=args)

    def count(low, high=None):
        if not low <= len(args) <= (low if high is None else high):
            raise CommandError("Invalid arguments; see /help.")

    action = None
    if command in ('income', 'expense'):
        fields = ['amount_inr', 'description'] if command == 'income' else ['amount_inr', 'bucket_name', 'description']
        if len(args) < len(fields):
            _missing(fields[len(args):])
        start = 1 if command == 'income' else 2
        description_parts = args[start:]
        expression = None
        if description_parts and (description_parts[-1] in ('today', 'yesterday') or re.fullmatch(r'\d{4}-\d{2}-\d{2}', description_parts[-1])):
            expression = _date(description_parts.pop())
        if not description_parts:
            _missing(['description'])
        action = dict(type=command, amount_inr=_money(args[0]), description=_bounded(' '.join(description_parts), 240, 'Description'), date_expression=expression)
        if command == 'expense':
            action['bucket_name'] = _bounded(args[1], 60, 'Bucket')
    elif command == 'bucket':
        count(1)
        action = dict(type='create_bucket', name=_bounded(args[0], 60, 'Bucket'))
    elif command == 'allocate':
        count(2)
        action = dict(type='allocate', amount_inr=_money(args[0]), bucket_name=_bounded(args[1], 60, 'Bucket'))
    elif command == 'transfer':
        count(3)
        action = dict(type='transfer', amount_inr=_money(args[0]), source_bucket=_bounded(args[1], 60, 'Bucket'), destination_bucket=_bounded(args[2], 60, 'Bucket'))
    elif command == 'undo':
        count(0, 1)
        action = {'type': 'undo', **({'last': True} if not args or args[0] == 'last' else {'transaction_id': args[0]})}
    elif command == 'correct':
        count(2, 7)
        names = {'amount': 'amount_inr', 'bucket': 'bucket_name', 'date': 'date_expression', 'description': 'description', 'source': 'source_bucket', 'destination': 'destination_bucket'}
        changes = {}
        for item in args[1:]:
            key, separator, value = item.partition('=')
            if not separator or key not in names or names[key] in changes:
                raise CommandError('Unknown or duplicate correction field.')
            if key == 'amount':
                value = _money(value)
            elif key == 'date':
                value = _date(value)
            else:
                value = _bounded(value, 240 if key == 'description' else 60, key)
            changes[names[key]] = value
        action = dict(type='correct', transaction_id=args[0], changes=changes)
    elif command == 'target':
        count(2)
        action = dict(type='set_target', bucket_name=_bounded(args[0], 60, 'Bucket'))
        action.update({'remove': True} if args[1] == 'off' else {'amount_inr': _money(args[1])})
    elif command in ('balance', 'spending', 'calendar'):
        result['kind'] = 'query'
        query = dict(report={'balance': 'balances', 'spending': 'spending', 'calendar': 'calendar'}[command], period='month' if command == 'calendar' else 'today', start=None, end=None, bucket_name=None)
        if command != 'spending':
            count(0)
        else:
            count(1, 3)
            if args[0] in ('today', 'week', 'month'):
                count(1, 2)
                query['period'] = args[0]
                if len(args) == 2:
                    query['bucket_name'] = _bounded(args[1], 60, 'Bucket')
            else:
                count(2, 3)
                start, end = _date(args[0]), _date(args[1])
                if start in ('today', 'yesterday') or end in ('today', 'yesterday') or start > end:
                    raise CommandError('Range requires ordered ISO dates.')
                query.update(period='range', start=start, end=end)
                if len(args) == 3:
                    query['bucket_name'] = _bounded(args[2], 60, 'Bucket')
        result['query'] = query
    elif command in ('start', 'invite', 'users', 'revoke', 'timezone', 'cancel', 'help'):
        count(0, 1) if command == 'start' else count(1 if command in ('revoke', 'timezone') else 0)
        result['kind'] = {'start': 'setup', 'invite': 'admin', 'users': 'admin', 'revoke': 'admin', 'timezone': 'timezone', 'cancel': 'cancel', 'help': 'help'}[command]
        if command == 'revoke' and not re.fullmatch(r'[1-9][0-9]*', args[0]):
            raise CommandError('Telegram ID must be a positive integer.')
        if command == 'timezone':
            try:
                ZoneInfo(args[0])
            except (ZoneInfoNotFoundError, ValueError):
                raise CommandError('Unknown timezone.') from None
    else:
        raise CommandError('Unsupported command; see /help.')
    if action is not None:
        result['actions'] = [action]
    return result
