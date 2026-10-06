"""Administrator access UI only; invite redemption belongs to parent /start."""
import re
from datetime import datetime

from budget_bot.domain.errors import BudgetError


class AccessService:
    def __init__(self, store):
        self.store = store

    def handle_admin(self, owner_id, command, args, now):
        """Accept the command parser's command and list of string arguments.

        BudgetStore authorizes every operation. No snapshots, account balances,
        transactions or financial mutations are accessed by this service.
        """
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise BudgetError('invalid_datetime', 'An aware receipt timestamp is required.')
        # This access-only call also gates malformed/unsupported admin requests.
        users = self.store.list_access(owner_id)
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise BudgetError('invalid_command', 'Use /invite, /users or /revoke telegram-id.')
        if command == 'invite' and not args:
            code = self.store.create_invite(owner_id, now)
            message = f'Single-use invite, valid for 24 hours. Send the recipient: /start {code}'
        elif command == 'users' and not args:
            lines = ['Registered access:']
            for user in users:
                status = 'active' if user['active'] else 'revoked'
                role = 'administrator' if user['admin'] else 'member'
                lines.append(f'{user["telegram_id"]}: {status} ({role})')
            message = '\n'.join(lines)
        elif command == 'revoke' and len(args) == 1:
            value = args[0]
            if not re.fullmatch(r'[1-9][0-9]{0,18}', value) or int(value) > 2**63 - 1:
                raise BudgetError('invalid_user', 'A positive Telegram user ID is required.')
            self.store.revoke_user(owner_id, int(value), now)
            message = f'Access revoked for Telegram user {value}.'
        else:
            raise BudgetError('invalid_command', 'Use /invite, /users or /revoke telegram-id.')
        return {'text': message, 'keyboard': []}
